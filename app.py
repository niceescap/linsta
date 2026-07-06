#!/usr/bin/env python3
from flask import Flask, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

from config import SECRET_KEY, FLASK_HOST, FLASK_PORT, FLASK_DEBUG
from db import get_db
from email_utils import generate_verification_token, confirm_verification_token, send_verification_email

app = Flask(__name__)
app.secret_key = SECRET_KEY


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
        # Le compte est créé même si le mail échoue — à surveiller en logs
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


if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
