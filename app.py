#!/usr/bin/env python3
from datetime import timedelta
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import (Flask, request, jsonify, session, redirect, url_for,
                   render_template, send_file)
from werkzeug.security import generate_password_hash, check_password_hash

from config import (
    SECRET_KEY, FLASK_HOST, FLASK_PORT, FLASK_DEBUG,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    SESSION_LIFETIME_DAYS,
)
from db import get_db
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
        if "user_id" not in session:
            return jsonify({"error": "non connecté"}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "non connecté"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "admin requis"}), 403
        return f(*args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
# ROOT — état de session (à remplacer par index.html plus tard)
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    user = None
    if "user_id" in session:
        user = {
            "id": session["user_id"],
            "display_name": session.get("display_name"),
            "role": session.get("role"),
        }
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
    session.clear()
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
    session.clear()
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
    token = google.authorize_access_token()
    userinfo = token.get("userinfo") or google.userinfo()

    email = (userinfo.get("email") or "").strip().lower()
    google_id = userinfo.get("sub")
    display_name = (userinfo.get("name") or email).strip()

    if not email or not google_id:
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

    session.permanent = True
    session["user_id"] = user_id
    session["display_name"] = display_name
    session["role"] = user_role

    return redirect("/")


# ─────────────────────────────────────────────
# UPLOAD DE TRACKS
# ─────────────────────────────────────────────

import os
import time
import uuid
from mutagen import File as MutagenFile
from config import TRACKS_PATH, STORAGE_PATH


@app.route("/tracks", methods=["POST"])
@login_required
def upload_track():
    file = request.files.get("file")
    if not file or not file.filename:
        return redirect("/?err=Fichier+manquant")

    if not file.filename.lower().endswith(".mp3"):
        return redirect("/?err=Seuls+les+MP3+sont+accept%C3%A9s")

    title = (request.form.get("title") or "").strip()
    artist = (request.form.get("artist") or "").strip()
    source_url = (request.form.get("source_url") or "").strip()
    try:
        genre_id = int(request.form.get("genre_id") or 0)
    except ValueError:
        genre_id = 0

    if not title or not genre_id:
        return redirect("/?err=Titre+et+genre+obligatoires")

    db = get_db()
    genre = db.execute("SELECT id FROM genres WHERE id = ?", (genre_id,)).fetchone()
    if not genre:
        db.close()
        return redirect("/?err=Genre+invalide")

    ext = os.path.splitext(file.filename)[1].lower()
    stored_name = f"{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(TRACKS_PATH, stored_name)
    file.save(abs_path)

    file_size = os.path.getsize(abs_path)
    rel_path = os.path.join("tracks", stored_name)

    duration = None
    try:
        audio = MutagenFile(abs_path)
        if audio is not None and audio.info is not None:
            duration = float(audio.info.length)
    except Exception:
        pass

    cur = db.execute(
        """INSERT INTO tracks
           (uploader_id, file_path, file_size, original_filename,
            title, artist, genre_id, duration_seconds, source_url, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (session["user_id"], rel_path, file_size, file.filename,
         title, artist, genre_id, duration, source_url or None),
    )
    track_id = cur.lastrowid
    db.commit()
    db.close()

    # Récupération de l'image OG (timeout court, échec silencieux)
    if source_url:
        try:
            img_name = download_og_image(source_url, track_id, META_PATH)
            if img_name:
                db = get_db()
                db.execute("UPDATE tracks SET meta_path = ? WHERE id = ?",
                           (img_name, track_id))
                db.commit()
                db.close()
        except Exception:
            pass

    return redirect("/?ok=Track+envoy%C3%A9%2C+en+attente+de+validation")


# ─────────────────────────────────────────────
# ADMIN — VALIDATION DES TRACKS
# ─────────────────────────────────────────────

@app.route("/admin", methods=["GET"])
@admin_required
def admin_index():
    status_filter = request.args.get("status")
    db = get_db()
    query = """
        SELECT t.*, g.label AS genre_label,
               u.display_name AS uploader_name, u.email AS uploader_email
        FROM tracks t
        LEFT JOIN genres g ON g.id = t.genre_id
        LEFT JOIN users  u ON u.id = t.uploader_id
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


@app.route("/admin/tracks/<int:track_id>/approve", methods=["POST"])
@admin_required
def approve_track(track_id):
    db = get_db()
    track = db.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if not track:
        db.close()
        return redirect("/admin?err=Track+introuvable")
    db.execute(
        """UPDATE tracks
           SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP,
               rejection_reason=NULL, updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (session["user_id"], track_id),
    )
    db.commit()
    db.close()
    return redirect("/admin?ok=Track+valid%C3%A9")


@app.route("/admin/tracks/<int:track_id>/reject", methods=["POST"])
@admin_required
def reject_track(track_id):
    reason = (request.form.get("reason") or "").strip()
    db = get_db()
    track = db.execute("SELECT id FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if not track:
        db.close()
        return redirect("/admin?err=Track+introuvable")
    db.execute(
        """UPDATE tracks
           SET status='rejected', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP,
               rejection_reason=?, updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (session["user_id"], reason or None, track_id),
    )
    db.commit()
    db.close()
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
    track = db.execute("SELECT meta_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
    db.close()
    if not track or not track["meta_path"]:
        return redirect("/static/radio-logo.png")
    abs_path = os.path.join(META_PATH, track["meta_path"])
    if not os.path.exists(abs_path):
        return redirect("/static/radio-logo.png")
    return send_file(abs_path)


@app.route("/admin/tracks/<int:track_id>/refetch-cover", methods=["POST"])
@admin_required
def refetch_cover(track_id):
    db = get_db()
    track = db.execute(
        "SELECT source_url FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    if not track:
        db.close()
        return redirect("/admin?err=Track+introuvable")
    if not track["source_url"]:
        db.close()
        return redirect("/admin?err=Pas+d%27URL+source+pour+ce+track")

    img_name = download_og_image(track["source_url"], track_id, META_PATH)
    if img_name:
        db.execute("UPDATE tracks SET meta_path = ? WHERE id = ?", (img_name, track_id))
        db.commit()
        db.close()
        return redirect("/admin?ok=Image+r%C3%A9cup%C3%A9r%C3%A9e")
    db.close()
    return redirect("/admin?err=%C3%89chec+de+r%C3%A9cup%C3%A9ration+%28OG+introuvable%29")


@app.route("/admin/tracks/<int:track_id>/reintegrate", methods=["POST"])
@admin_required
def reintegrate_track(track_id):
    db = get_db()
    track = db.execute("SELECT id, status FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if not track:
        db.close()
        return redirect("/admin?err=Track+introuvable")
    db.execute(
        """UPDATE tracks
           SET status='approved', reviewed_by=?, reviewed_at=CURRENT_TIMESTAMP,
               rejection_reason=NULL, updated_at=CURRENT_TIMESTAMP
           WHERE id=?""",
        (session["user_id"], track_id),
    )
    db.commit()
    db.close()
    return redirect("/admin?status=rejected&ok=Track+r%C3%A9int%C3%A9gr%C3%A9")


@app.route("/admin/tracks/<int:track_id>/delete", methods=["POST"])
@admin_required
def delete_track(track_id):
    db = get_db()
    track = db.execute(
        "SELECT id, file_path, meta_path FROM tracks WHERE id = ?", (track_id,)
    ).fetchone()
    if not track:
        db.close()
        return redirect("/admin?err=Track+introuvable")

    # Supprime d'abord les diffusions liées (FK sans CASCADE dans le schéma)
    db.execute("DELETE FROM plays WHERE track_id = ?", (track_id,))
    db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    db.commit()
    db.close()

    # Efface le MP3 et la cover du disque (silencieux si absents)
    for rel in (track["file_path"], track["meta_path"]):
        if not rel:
            continue
        # file_path contient déjà "tracks/xxx.mp3", meta_path contient juste "1.jpg"
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
