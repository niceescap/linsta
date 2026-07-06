import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from itsdangerous import URLSafeTimedSerializer

from config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
    SECRET_KEY, APP_BASE_URL, VERIFICATION_TOKEN_MAX_AGE
)

serializer = URLSafeTimedSerializer(SECRET_KEY)


def generate_verification_token(email: str) -> str:
    return serializer.dumps(email, salt="email-verification")


def confirm_verification_token(token: str, max_age: int = VERIFICATION_TOKEN_MAX_AGE):
    """Retourne l'email si le token est valide, sinon None."""
    try:
        return serializer.loads(token, salt="email-verification", max_age=max_age)
    except Exception:
        return None


def send_verification_email(to_email: str, token: str):
    """Envoie le mail de validation de compte via TEM."""
    verify_link = f"{APP_BASE_URL}/verify/{token}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Confirme ton compte"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    text = f"Clique sur ce lien pour valider ton compte : {verify_link}"
    html = f"""
    <p>Bienvenue !</p>
    <p><a href="{verify_link}">Clique ici pour valider ton compte</a></p>
    <p>Ce lien expire dans 24h.</p>
    """
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to_email], msg.as_string())
