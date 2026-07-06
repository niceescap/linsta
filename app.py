#!/usr/bin/env python3
import re
import sqlite3

from flask import Flask, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

from config import SECRET_KEY, FLASK_HOST, FLASK_PORT, FLASK_DEBUG
from db import get_db
from email_utils import generate_verification_token, confirm_verification_token, send_verification_email

app = Flask(__name__)
app.secret_key = SECRET_KEY


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def current_user_id():
    """Retourne l'id de l'utilisateur connecté ou None."""
    return session.get("user_id")


# ─────────────────────────────────────────────
# REGISTER
# ─────────────────────────────────────────────

@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    display_name = data.get("display_name", "")

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
        (email, password_hash, display_name, token)
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

    db.execute("UPDATE users SET is_verified = 1, verification_token = NULL WHERE id = ?", (user["id"],))
    db.commit()
    db.close()
    return jsonify({"ok": True, "message": "compte validé, tu peux te connecter"})


# ─────────────────────────────────────────────
# LOGIN / LOGOUT
# ─────────────────────────────────────────────

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    db.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "identifiants invalides"}), 401

    if not user["is_verified"]:
        return jsonify({"error": "compte non validé, vérifie ta boîte mail"}), 403

    session["user_id"] = user["id"]
    session["display_name"] = user["display_name"]
    return jsonify({"ok": True, "user": {"id": user["id"], "display_name": user["display_name"]}})


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/me", methods=["GET"])
def me():
    if "user_id" not in session:
        return jsonify({"error": "non connecté"}), 401
    return jsonify({"user_id": session["user_id"], "display_name": session.get("display_name")})


# ═════════════════════════════════════════════
# BOTS — cœur du réseau social de LLM
# ═════════════════════════════════════════════

# ── Créer son bot (1 seul/user) ──
@app.route("/bots", methods=["POST"])
def create_bot():
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "non connecté"}), 401

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    system_prompt = data.get("system_prompt", "")
    slug = (data.get("slug") or "").strip()
    try:
        temperature = float(data.get("temperature", 0.2))
    except (TypeError, ValueError):
        temperature = 0.2

    if not name:
        return jsonify({"error": "name requis"}), 400

    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        return jsonify({"error": "slug invalide (génère-le manuellement)"}), 400

    db = get_db()
    deja = db.execute("SELECT id FROM bots WHERE owner_id = ?", (uid,)).fetchone()
    if deja:
        db.close()
        return jsonify({"error": "tu as déjà un bot (1 seul autorisé)"}), 409

    try:
        cur = db.execute(
            """INSERT INTO bots (owner_id, name, slug, description, system_prompt, temperature)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, name, slug, description, system_prompt, temperature)
        )
        db.commit()
        bot_id = cur.lastrowid
    except sqlite3.IntegrityError:
        db.close()
        return jsonify({"error": "slug déjà pris"}), 409
    db.close()
    return jsonify({"ok": True, "bot": {"id": bot_id, "slug": slug}}), 201


# ── Feed public des bots publiés ──
@app.route("/bots", methods=["GET"])
def list_bots():
    db = get_db()
    rows = db.execute(
        """SELECT b.id, b.name, b.slug, b.description, b.temperature, b.published, b.created_at,
                  u.display_name AS owner_name,
                  (SELECT COUNT(*) FROM likes l WHERE l.bot_id = b.id) AS like_count,
                  (SELECT COUNT(*) FROM comments c WHERE c.bot_id = b.id) AS comment_count
           FROM bots b JOIN users u ON u.id = b.owner_id
           WHERE b.published = 1
           ORDER BY b.created_at DESC"""
    ).fetchall()
    db.close()
    return jsonify({"bots": [dict(r) for r in rows]})


# ── Détail public d'un bot (par slug) ──
@app.route("/bots/<slug>", methods=["GET"])
def get_bot(slug):
    db = get_db()
    bot = db.execute(
        """SELECT b.*, u.display_name AS owner_name,
                  (SELECT COUNT(*) FROM likes l WHERE l.bot_id = b.id) AS like_count,
                  (SELECT COUNT(*) FROM comments c WHERE c.bot_id = b.id) AS comment_count
           FROM bots b JOIN users u ON u.id = b.owner_id
           WHERE b.slug = ?""",
        (slug,)
    ).fetchone()
    if not bot:
        db.close()
        return jsonify({"error": "bot introuvable"}), 404
    db.close()
    return jsonify({"bot": dict(bot)})


# ── Éditer son bot ──
@app.route("/bots/<int:bot_id>", methods=["PUT"])
def update_bot(bot_id):
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "non connecté"}), 401
    db = get_db()
    bot = db.execute("SELECT owner_id FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not bot:
        db.close()
        return jsonify({"error": "bot introuvable"}), 404
    if bot["owner_id"] != uid:
        db.close()
        return jsonify({"error": "non autorisé"}), 403

    data = request.get_json() or {}
    fields, vals = [], []
    if "name" in data:
        fields.append("name = ?"); vals.append(data["name"])
    if "description" in data:
        fields.append("description = ?"); vals.append(data["description"])
    if "system_prompt" in data:
        fields.append("system_prompt = ?"); vals.append(data["system_prompt"])
    if "temperature" in data:
        try:
            fields.append("temperature = ?"); vals.append(float(data["temperature"]))
        except (TypeError, ValueError):
            pass
    if not fields:
        db.close()
        return jsonify({"error": "rien à modifier"}), 400

    fields.append("updated_at = CURRENT_TIMESTAMP")
    vals.append(bot_id)
    db.execute("UPDATE bots SET " + ", ".join(fields) + " WHERE id = ?", vals)
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Supprimer son bot ──
@app.route("/bots/<int:bot_id>", methods=["DELETE"])
def delete_bot(bot_id):
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "non connecté"}), 401
    db = get_db()
    bot = db.execute("SELECT owner_id FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not bot:
        db.close()
        return jsonify({"error": "bot introuvable"}), 404
    if bot["owner_id"] != uid:
        db.close()
        return jsonify({"error": "non autorisé"}), 403
    db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ── Publier / dépublier (toggle) ──
@app.route("/bots/<int:bot_id>/publish", methods=["POST"])
def publish_bot(bot_id):
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "non connecté"}), 401
    db = get_db()
    bot = db.execute("SELECT owner_id, published FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not bot:
        db.close()
        return jsonify({"error": "bot introuvable"}), 404
    if bot["owner_id"] != uid:
        db.close()
        return jsonify({"error": "non autorisé"}), 403
    new_state = 0 if bot["published"] else 1
    db.execute("UPDATE bots SET published = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (new_state, bot_id))
    db.commit()
    db.close()
    return jsonify({"ok": True, "published": bool(new_state)})


# ── Lister les commentaires (public) ──
@app.route("/bots/<int:bot_id>/comments", methods=["GET"])
def list_comments(bot_id):
    db = get_db()
    rows = db.execute(
        """SELECT c.id, c.content, c.created_at, u.display_name AS author
           FROM comments c JOIN users u ON u.id = c.user_id
           WHERE c.bot_id = ? ORDER BY c.created_at ASC"""
    ).fetchall()
    db.close()
    return jsonify({"comments": [dict(r) for r in rows]})


# ── Ajouter un commentaire ──
@app.route("/bots/<int:bot_id>/comments", methods=["POST"])
def add_comment(bot_id):
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "non connecté"}), 401
    data = request.get_json() or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "commentaire vide"}), 400
    db = get_db()
    bot = db.execute("SELECT id FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not bot:
        db.close()
        return jsonify({"error": "bot introuvable"}), 404
    cur = db.execute("INSERT INTO comments (bot_id, user_id, content) VALUES (?, ?, ?)",
                     (bot_id, uid, content))
    db.commit()
    cid = cur.lastrowid
    db.close()
    return jsonify({"ok": True, "comment_id": cid}), 201


# ── Liker / unliker (toggle) ──
@app.route("/bots/<int:bot_id>/like", methods=["POST"])
def toggle_like(bot_id):
    uid = current_user_id()
    if not uid:
        return jsonify({"error": "non connecté"}), 401
    db = get_db()
    bot = db.execute("SELECT id FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not bot:
        db.close()
        return jsonify({"error": "bot introuvable"}), 404
    existing = db.execute("SELECT id FROM likes WHERE bot_id = ? AND user_id = ?",
                          (bot_id, uid)).fetchone()
    if existing:
        db.execute("DELETE FROM likes WHERE bot_id = ? AND user_id = ?", (bot_id, uid))
        liked = False
    else:
        db.execute("INSERT INTO likes (bot_id, user_id) VALUES (?, ?)", (bot_id, uid))
        liked = True
    db.commit()
    db.close()
    return jsonify({"ok": True, "liked": liked})


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
