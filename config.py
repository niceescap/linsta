import os
from dotenv import load_dotenv

load_dotenv()  # charge les variables depuis .env s'il existe

# ─────────────────────────────────────────────
# FLASK
# ─────────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5050
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true")

# ─────────────────────────────────────────────
# SECRET KEY — OBLIGATOIRE en production
# ─────────────────────────────────────────────
SECRET_KEY = os.environ.get("LIQUID_SECRET_KEY")
if not SECRET_KEY:
    # Placeholder acceptable uniquement en dev local (debug actif).
    SECRET_KEY = "dev-insecure-secret-change-me"
    if not FLASK_DEBUG:
        raise RuntimeError(
            "LIQUID_SECRET_KEY non défini en production — expose un secret par défaut. "
            "Ajoute LIQUID_SECRET_KEY dans ton .env (ignoré par Git)."
        )

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "liquid.db")

# ─────────────────────────────────────────────
# SMTP — Scaleway TEM (mêmes identifiants que fu19)
# À renseigner via variables d'environnement, jamais en dur dans le code.
# ─────────────────────────────────────────────
SMTP_HOST = os.environ.get("TEM_SMTP_HOST", "smtp.tem.scw.cloud")
SMTP_PORT = int(os.environ.get("TEM_SMTP_PORT", 587))
SMTP_USER = os.environ.get("TEM_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("TEM_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("TEM_SMTP_FROM", "no-reply@fu19.org")

# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────
APP_BASE_URL = os.environ.get("LIQUID_BASE_URL", "http://localhost:5050")
VERIFICATION_TOKEN_MAX_AGE = 60 * 60 * 24  # 24h, en secondes
