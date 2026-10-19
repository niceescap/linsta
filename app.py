#!/usr/bin/env python3
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps

from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from flask import (Flask, request, jsonify, session, redirect, url_for,
                   render_template, send_file, g)
from werkzeug.security import generate_password_hash, check_password_hash

from config import (
    SECRET_KEY, FLASK_HOST, FLASK_PORT, FLASK_DEBUG,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    SESSION_LIFETIME_DAYS,
)
from db import get_db
from web_security import init_web_security, clear_login_session
from audio_storage import MediaLimits, StorageError, checked_open
from submission_audio_routes import audio_preflight, create_audio_blueprint
import os
from email_utils import (
    generate_verification_token,
    confirm_verification_token,
    send_verification_email,
)

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Session persistante : indispensable pour Safari iOS qui purge sinon le cookie
# dès que l'app passe en arrière-plan. Secure conditionné à prod (HTTPS).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not FLASK_DEBUG,
    PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_LIFETIME_DAYS),
)
media_limits = MediaLimits.from_environment(os.environ)
app.config.update(MEDIA_LIMITS=media_limits, MAX_CONTENT_LENGTH=media_limits.request_bytes,
                  MAX_FORM_MEMORY_SIZE=500000, MAX_FORM_PARTS=20)
init_web_security(app, lambda: get_db(), pre_body_check=lambda: audio_preflight(lambda: get_db()))


@app.errorhandler(413)
def oversized_request(error):
    return jsonify(error='La requête dépasse la taille autorisée.', code='file_too_large'), 413

# Derrière nginx : faire confiance aux en-têtes X-Forwarded-* pour que
# url_for(_external=True) génère les bonnes URLs (https://fgl.servemp3.com).
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# ─────────────────────────────────────────────
# OAUTH GOOGLE
# ─────────────────────────────────────────────

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Emails promus automatiquement admin à la première connexion Google.
ADMIN_EMAILS = {
    "futuregroovelabrecords@gmail.com",
    "niceescap@gmail.com",
}


# ─────────────────────────────────────────────
# DÉCORATEURS
# ─────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.current_user is None:
            if g.account_unverified:
                return jsonify({"error": "compte non validé"}), 403
            return jsonify({"error": "non connecté"}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if g.current_user is None:
            if g.account_unverified:
                return jsonify({"error": "compte non validé"}), 403
            return jsonify({"error": "non connecté"}), 401
        if g.current_user['role'] != "admin":
            return jsonify({"error": "admin requis"}), 403
        return f(*args, **kwargs)
    return wrapper


from submission_routes import create_submission_blueprint
app.register_blueprint(create_submission_blueprint(lambda: get_db(), login_required))
from submission_review_routes import create_submission_review_blueprint
app.register_blueprint(create_submission_review_blueprint(lambda: get_db(), admin_required))
app.register_blueprint(create_audio_blueprint(lambda: get_db(), lambda: (STORAGE_PATH, META_PATH),
                                            login_required, admin_required))


# ─────────────────────────────────────────────
# ROOT — état de session (à remplacer par index.html plus tard)
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    user = g.current_user
    db = get_db()
    genres = db.execute("SELECT id, label FROM genres ORDER BY sort").fetchall()
    db.close()
    return render_template(
        "index.html",
        user=user,
        genres=genres,
        message=request.args.get("ok"),
        error=request.args.get("err"),
    )


@app.route("/logout-form", methods=["POST"])
def logout_form():
    clear_login_session()
    return redirect("/")


# ─────────────────────────────────────────────
# REGISTER (mot de passe)
# ─────────────────────────────────────────────

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip()

    if not email or not password:
        return jsonify({"error": "email et password requis"}), 400

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        db.close()
        return jsonify({"error": "email déjà utilisé"}), 409

    password_hash = generate_password_hash(password)
    token = generate_verification_token(email)

    db.execute(
        """INSERT INTO users (email, password_hash, display_name, verification_token, verification_sent_at)
           VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (email, password_hash, display_name, token),
    )
    db.commit()
    db.close()

    try:
        send_verification_email(email, token)
    except Exception as e:
        return jsonify({"ok": True, "warning": f"compte créé, mail non envoyé: {e}"}), 201

    return jsonify({"ok": True, "message": "compte créé, vérifie ta boîte mail"}), 201


# ─────────────────────────────────────────────
# VERIFY
# ─────────────────────────────────────────────

@app.route("/verify/<token>", methods=["GET"])
def verify(token):
    email = confirm_verification_token(token)
    if not email:
        return jsonify({"error": "lien invalide ou expiré"}), 400

    db = get_db()
    user = db.execute("SELECT id, is_verified FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        db.close()
        return jsonify({"error": "utilisateur introuvable"}), 404

    if user["is_verified"]:
        db.close()
        return jsonify({"ok": True, "message": "compte déjà validé"})

    db.execute(
        "UPDATE users SET is_verified = 1, verification_token = NULL WHERE id = ?",
        (user["id"],),
    )
    db.commit()
    db.close()
    return jsonify({"ok": True, "message": "compte validé, tu peux te connecter"})


# ─────────────────────────────────────────────
# LOGIN / LOGOUT / ME (mot de passe)
# ─────────────────────────────────────────────

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    db.close()

    if not user or not user["password_hash"] or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "identifiants invalides"}), 401

    if not user["is_verified"]:
        return jsonify({"error": "compte non validé, vérifie ta boîte mail"}), 403

    clear_login_session()
    session.permanent = True
    session["user_id"] = user["id"]
    session["display_name"] = user["display_name"]
    session["role"] = user["role"]

    return jsonify({
        "ok": True,
        "user": {
            "id": user["id"],
            "display_name": user["display_name"],
            "role": user["role"],
        },
    })


@app.route("/logout", methods=["POST"])
def logout():
    clear_login_session()
    return jsonify({"ok": True})


@app.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify({
        "user_id": session["user_id"],
        "display_name": session.get("display_name"),
        "role": session.get("role"),
    })


# ─────────────────────────────────────────────
# LOGIN GOOGLE (OAuth 2.0 / OIDC)
# ─────────────────────────────────────────────

@app.route("/login/google")
def login_google():
    redirect_uri = url_for("auth_google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_google_callback():
    try:
        # Authlib must consume and validate OAuth state before any account write.
        token = google.authorize_access_token()
    except OAuthError:
        return jsonify({"error": "réponse OAuth invalide"}), 400
    userinfo = token.get("userinfo") or google.userinfo()

    email = (userinfo.get("email") or "").strip().lower()
    google_id = userinfo.get("sub")
    display_name = (userinfo.get("name") or email).strip()

    if not email or not google_id or userinfo.get('email_verified') is not True:
        return jsonify({"error": "réponse Google incomplète"}), 400

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if user:
        # Compte existant : on lie le google_id si absent, on promeut si whitelisté.
        new_role = user["role"]
        if email in ADMIN_EMAILS and new_role != "admin":
            new_role = "admin"
        db.execute(
            """UPDATE users
               SET google_id = COALESCE(google_id, ?),
                   is_verified = 1,
                   role = ?,
                   display_name = COALESCE(display_name, ?)
               WHERE id = ?""",
            (google_id, new_role, display_name, user["id"]),
        )
        db.commit()
        user_id = user["id"]
        user_role = new_role
    else:
        # Nouveau compte : rôle par défaut, promotion si whitelisté.
        role = "admin" if email in ADMIN_EMAILS else "contributor"
        cur = db.execute(
            """INSERT INTO users (email, is_verified, display_name, role, google_id)
               VALUES (?, 1, ?, ?, ?)""",
            (email, display_name, role, google_id),
        )
        db.commit()
        user_id = cur.lastrowid
        user_role = role

    db.close()

    clear_login_session()
    session.permanent = True
    session["user_id"] = user_id
    session["display_name"] = display_name
    session["role"] = user_role

    return redirect("/")


# ─────────────────────────────────────────────
# ANCIEN DÉPÔT DIRECT — FERMÉ (parcours URL-first obligatoire)
# ─────────────────────────────────────────────

import os
import time
# META_PATH is also needed to serve and delete existing legacy cover files.
from config import STORAGE_PATH, META_PATH


@app.route("/tracks", methods=["POST"])
@login_required
def upload_track():
    # Keep authentication and global CSRF; never inspect/save audio or open a
    # write transaction here, even when an accepted submission ID is supplied.
    return jsonify({
        "code": "direct_upload_disabled",
        "error": "Le dépôt direct MP3 est fermé. Proposez un lien YouTube ou Spotify dans /submissions.",
        "submissions_url": url_for("contributions.dashboard"),
    }), 410


# ─────────────────────────────────────────────
# ADMIN — VALIDATION DES TRACKS
# ─────────────────────────────────────────────

@app.route("/admin", methods=["GET"])
@admin_required
def admin_index():
    status_filter = request.args.get("status")
    db = get_db()
    query = """
        SELECT t.*, s.id AS submission_id, g.label AS genre_label,
               u.display_name AS uploader_name, u.email AS uploader_email
        FROM tracks t
        LEFT JOIN genres g ON g.id = t.genre_id
        LEFT JOIN users  u ON u.id = t.uploader_id
        LEFT JOIN submissions s ON s.track_id = t.id
    """
    params = ()
    if status_filter in ("pending", "approved", "rejected"):
        query += " WHERE t.status = ?"
        params = (status_filter,)
    query += """
        ORDER BY
            CASE t.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
            t.created_at DESC
    """
    tracks = db.execute(query, params).fetchall()
    db.close()
    return render_template(
        "admin.html",
        user={"display_name": session.get("display_name"), "role": session.get("role")},
        tracks=tracks,
        filter=status_filter,
        message=request.args.get("ok"),
        error=request.args.get("err"),
    )


class LinkedSubmissionTrackError(Exception):
    def __init__(self, submission_id):
        self.submission_id = submission_id


@app.errorhandler(LinkedSubmissionTrackError)
def linked_submission_track_error(error):
    return jsonify({
        "code": "linked_submission_track",
        "error": "Ce morceau dépend d’une proposition : utilisez son parcours de validation audio.",
        "submission_url": url_for("submission_review.show", submission_id=error.submission_id),
    }), 409


@contextmanager
def _legacy_track_transaction(track_id):
    """Serialize link-check and mutation; legacy actions never touch linked audio.

    A concurrent attachment cannot slip between the check and a track update or
    deletion. No provider request or media deletion occurs inside this transaction.
    """
    conn = get_db()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            linked = conn.execute("SELECT id FROM submissions WHERE track_id=?", (track_id,)).fetchone()
            if linked:
                raise LinkedSubmissionTrackError(linked['id'])
            yield conn
    finally:
        conn.close()


@app.route("/admin/tracks/<int:track_id>/approve", methods=["POST"])
@admin_required
def approve_track(track_id):
    with _legacy_track_transaction(track_id) as db:
        track = db.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not track:
            return redirect("/admin?err=Track+introuvable")
        db.execute(
            """UPDATE tracks
               SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP,
                   rejection_reason=NULL, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (session["user_id"], track_id),
        )
    return redirect("/admin?ok=Track+valid%C3%A9")


@app.route("/admin/tracks/<int:track_id>/reject", methods=["POST"])
@admin_required
def reject_track(track_id):
    reason = (request.form.get("reason") or "").strip()
    with _legacy_track_transaction(track_id) as db:
        track = db.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not track:
            return redirect("/admin?err=Track+introuvable")
        db.execute(
            """UPDATE tracks
               SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP,
                   rejection_reason=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (session["user_id"], reason or None, track_id),
        )
    return redirect("/admin?ok=Track+rejet%C3%A9")


@app.route("/media/<int:track_id>", methods=["GET"])
@admin_required
def serve_track(track_id):
    db = get_db()
    track = db.execute(
        "SELECT file_path, mime_type FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    db.close()
    if not track:
        return jsonify({"error": "track introuvable"}), 404
    abs_path = os.path.join(STORAGE_PATH, track["file_path"])
    if not os.path.exists(abs_path):
        return jsonify({"error": "fichier manquant sur le disque"}), 404
    return send_file(abs_path, mimetype=track["mime_type"] or "audio/mpeg")


# ─────────────────────────────────────────────
# NOW PLAYING — métadonnées du track en cours
# ─────────────────────────────────────────────

import json
from config import HLS_PATH


@app.route("/now-playing", methods=["GET"])
def now_playing():
    state_file = os.path.join(HLS_PATH, "now.json")
    if not os.path.exists(state_file):
        return jsonify({"track_id": None, "message": "flux non démarré"})
    try:
        with open(state_file) as f:
            data = json.load(f)
    except Exception:
        return jsonify({"track_id": None, "message": "état illisible"})

    if data.get("track_id"):
        data["elapsed"] = max(0.0, time.time() - data.get("started_at", 0))
    else:
        data["elapsed"] = 0
    return jsonify(data)


@app.route("/media/cover/<int:track_id>", methods=["GET"])
def serve_cover(track_id):
    """Sert l'image de chaîne (OG) ou redirige vers le logo radio en fallback."""
    db = get_db()
    track = db.execute("""SELECT t.meta_path,t.status,s.id AS submission_id,s.status AS submission_status,b.id AS bundle_id,
        b.cover_original_path,b.cover_original_sha256,b.cover_original_size,b.cover_original_mime,
        b.cover_normalized_path,b.cover_normalized_sha256,b.cover_normalized_size,b.cover_normalized_mime,
        CASE WHEN a.id IS NOT NULL THEN digger_document_ready(a.document_version,a.document_text,a.document_sha256)
            ELSE 0 END AS rights_ready
        FROM tracks t LEFT JOIN submissions s ON s.track_id=t.id
        LEFT JOIN submission_media_bundles b ON b.submission_id=s.id
        LEFT JOIN submission_authorizations a ON a.bundle_id=b.id WHERE t.id=?""", (track_id,)).fetchone()
    db.close()
    # A previously approved linked track without a media bundle predates the new
    # evidence workflow. Preserve its existing cover; migration 003 prevents any
    # new source approval without a bundle. Pending/rejected covers stay private.
    legacy_approved = bool(track and track['bundle_id'] is None and track['status']=='approved'
                           and track['submission_status']=='approved')
    if track and track['submission_id'] is not None and not legacy_approved:
        if track['status']!='approved' or track['submission_status']!='approved' or not track['rights_ready']:
            response=jsonify(error='Cover indisponible avant validation finale.')
            response.headers['Cache-Control']='no-store'
            return response,404
        prefix='cover_normalized' if track['cover_normalized_path'] else 'cover_original'
        if not track[prefix+'_path'] or track['meta_path']!=track[prefix+'_path']:
            return jsonify(error='Cover autorisée indisponible.'),404
        try:
            stream,_=checked_open(META_PATH,track[prefix+'_path'],sha256=track[prefix+'_sha256'],size=track[prefix+'_size'])
        except StorageError:
            return jsonify(error='Cover autorisée indisponible.'),404
        try:
            response=send_file(stream,mimetype=track[prefix+'_mime'],download_name='cover',
                               conditional=False,etag=False,max_age=0)
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
            response.call_on_close(stream.close)
            return response
        except BaseException:
            stream.close()
            raise
    if not track or not track["meta_path"]:
        return redirect("/static/radio-logo.png")
    abs_path = os.path.join(META_PATH, track["meta_path"])
    if not os.path.exists(abs_path):
        return redirect("/static/radio-logo.png")
    return send_file(abs_path)


@app.route("/admin/tracks/<int:track_id>/refetch-cover", methods=["POST"])
@admin_required
def refetch_cover(track_id):
    with _legacy_track_transaction(track_id) as db:
        track = db.execute(
            "SELECT source_url FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        if not track:
            return redirect("/admin?err=Track+introuvable")
        if not track["source_url"]:
            return redirect("/admin?err=Pas+d%27URL+source+pour+ce+track")

    # Pre-existing missing download_og_image integration is intentionally not
    # activated in lot 6D: restoring generic provider fetching needs its own review.
    img_name = download_og_image(track["source_url"], track_id, META_PATH)
    if img_name:
        with _legacy_track_transaction(track_id) as db:
            db.execute("UPDATE tracks SET meta_path = ? WHERE id = ?", (img_name, track_id))
        return redirect("/admin?ok=Image+r%C3%A9cup%C3%A9r%C3%A9e")
    return redirect("/admin?err=%C3%89chec+de+r%C3%A9cup%C3%A9ration+%28OG+introuvable%29")


@app.route("/admin/tracks/<int:track_id>/reintegrate", methods=["POST"])
@admin_required
def reintegrate_track(track_id):
    with _legacy_track_transaction(track_id) as db:
        track = db.execute("SELECT id, status FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not track:
            return redirect("/admin?err=Track+introuvable")
        db.execute(
            """UPDATE tracks
               SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP,
                   rejection_reason=NULL, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (session["user_id"], track_id),
        )
    return redirect("/admin?status=rejected&ok=Track+r%C3%A9int%C3%A9gr%C3%A9")


@app.route("/admin/tracks/<int:track_id>/delete", methods=["POST"])
@admin_required
def delete_track(track_id):
    with _legacy_track_transaction(track_id) as db:
        track = db.execute(
            "SELECT id, file_path, meta_path FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        if not track:
            return redirect("/admin?err=Track+introuvable")
        # Preserve the existing explicit legacy deletion action. Linked audio is
        # refused above, before either history, track or file can be removed.
        db.execute("DELETE FROM plays WHERE track_id = ?", (track_id,))
        db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    # Efface le MP3 et la cover du disque (silencieux si absents), après COMMIT.
    for rel in (track["file_path"], track["meta_path"]):
        if not rel:
            continue
        if rel.startswith("tracks/") or rel.startswith("meta/"):
            abs_path = os.path.join(STORAGE_PATH, rel)
        else:
            abs_path = os.path.join(META_PATH, rel)
        try:
            if os.path.exists(abs_path):
                os.unlink(abs_path)
        except Exception:
            pass
    return redirect("/admin?status=rejected&ok=Track+supprim%C3%A9+d%C3%A9finitivement")


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
