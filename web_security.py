"""Flask account checks and session-bound, expiring CSRF tokens.

No database initialization and no network access. Existing OAuth GET endpoints
retain Authlib's state protocol; email verification retains its signed URL.
All POST/PUT/PATCH/DELETE requests, including login/register/logout, require CSRF.
JSON clients first GET /csrf-token and send X-CSRF-Token with their session cookie.
"""
import hmac
import secrets

from flask import current_app, g, jsonify, request, session
from itsdangerous import BadData, URLSafeTimedSerializer


def rotate_csrf_token():
    session['_csrf_nonce'] = secrets.token_urlsafe(32)


def csrf_token():
    g.csrf_token_rendered = True
    nonce = session.get('_csrf_nonce')
    if not isinstance(nonce, str):
        rotate_csrf_token()
        nonce = session['_csrf_nonce']
    return URLSafeTimedSerializer(current_app.secret_key, salt='digger-csrf-v1').dumps(nonce)


def clear_login_session():
    session.clear()
    rotate_csrf_token()


def init_web_security(app, connection_factory, *, pre_body_check=None):
    app.config.setdefault('CSRF_MAX_AGE', 3600)
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.before_request
    def load_current_account():
        g.current_user = None
        g.account_unverified = False
        uid = session.get('user_id')
        if uid is None:
            return
        if type(uid) is not int or not 0 < uid <= 2**63 - 1:
            clear_login_session()
            return
        conn = connection_factory()
        try:
            row = conn.execute("""SELECT id,email,display_name,role,is_verified
                FROM users WHERE id=?""", (uid,)).fetchone()
        finally:
            conn.close()
        if row is None or not row['is_verified']:
            g.account_unverified = row is not None
            clear_login_session()
            return
        g.current_user = dict(row)
        # Compatibility for existing routes reading session display/role fields.
        session['role'] = row['role']
        session['display_name'] = row['display_name']

    if pre_body_check is not None:
        app.before_request(pre_body_check)

    @app.before_request
    def protect_csrf():
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE') or request.url_rule is None:
            return
        supplied = request.headers.get('X-CSRF-Token')
        if supplied is None:
            tokens = request.form.getlist('csrf_token')
            supplied = tokens[0] if len(tokens) == 1 else None
        nonce = session.get('_csrf_nonce')
        valid = False
        if isinstance(supplied, str) and len(supplied) <= 2048 and isinstance(nonce, str):
            try:
                decoded = URLSafeTimedSerializer(app.secret_key, salt='digger-csrf-v1').loads(
                    supplied, max_age=app.config['CSRF_MAX_AGE'])
                valid = isinstance(decoded, str) and hmac.compare_digest(decoded.encode(), nonce.encode())
            except BadData:
                pass
        if not valid:
            return jsonify({'error': 'Jeton CSRF absent, invalide ou expiré.', 'code': 'csrf_invalid'}), 400

    @app.get('/csrf-token')
    def csrf_token_endpoint():
        result = jsonify({'csrf_token': csrf_token()})
        result.headers['Cache-Control'] = 'no-store'
        result.vary.add('Cookie')
        return result

    @app.after_request
    def prevent_token_caching(response):
        if getattr(g, 'csrf_token_rendered', False):
            response.headers['Cache-Control'] = 'no-store'
            response.vary.add('Cookie')
        return response
