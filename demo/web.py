"""WSGI entry point, only for the isolated demo launcher."""
from demo.runtime import install_config, require_initialized

install_config()
require_initialized()

from app import app as application
from flask import jsonify, render_template_string, request
from jinja2 import ChoiceLoader, FileSystemLoader
from pathlib import Path

application.jinja_loader = ChoiceLoader([
    FileSystemLoader(str(Path(__file__).with_name('templates'))), application.jinja_loader])

application.config.update(SESSION_COOKIE_NAME='digger_demo_session',
                          MAX_CONTENT_LENGTH=16 * 1024 * 1024)
from audio_storage import MediaLimits
application.config['MEDIA_LIMITS'] = MediaLimits(audio_bytes=10 * 1024 * 1024,
    cover_bytes=4 * 1024 * 1024, duration_seconds=600, cover_pixels=4_000_000,
    cover_dimension=4096)


@application.before_request
def demo_only():
    if request.path.startswith('/media/cover/'):
        return jsonify(error='Cover publique indisponible dans cette démonstration.'), 404
    if request.path in ('/register', '/login/google', '/auth/callback') or request.path.startswith('/verify/'):
        return jsonify(error='Inscription, SMTP et OAuth désactivés dans cette démonstration.'), 403


def demo_now_playing():
    # Do not expose arbitrary now.json fields while the live is deliberately closed.
    response = jsonify(track_id=None, message='Le direct est désactivé dans cette démonstration.')
    response.headers['Cache-Control'] = 'no-store'
    return response


application.view_functions['now_playing'] = demo_now_playing


@application.after_request
def demo_response_privacy(response):
    if request.path != '/static/radio-logo.png' and request.path != '/static/alive-og.jpg':
        response.headers['Cache-Control'] = 'no-store'
        response.vary.add('Cookie')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@application.get('/demo/login')
def demo_login():
    return render_template_string('''<!doctype html><html lang="fr"><meta charset="utf-8">
<title>Connexion Digger démo</title><h1>Connexion à la démonstration</h1>
<form id="login"><label>Email <input name="email" type="email" required autocomplete="username"></label>
<label>Mot de passe <input name="password" type="password" required autocomplete="current-password"></label>
<button>Se connecter</button></form><p id="result" role="status"></p>
<script>
document.querySelector('#login').addEventListener('submit', async event => {
 event.preventDefault();
 try {
  const form = new FormData(event.target);
  const response = await fetch('/login', {method:'POST', headers:{
   'Content-Type':'application/json', 'X-CSRF-Token':{{ csrf_token()|tojson }}},
   body:JSON.stringify({email:form.get('email'),password:form.get('password')})});
  if (response.ok) { location.href='/submissions'; return; }
  document.querySelector('#result').textContent='Connexion refusée. Rechargez la page avant de réessayer.';
 } catch (_) { document.querySelector('#result').textContent='Connexion indisponible.'; }
});
</script></html>''')
