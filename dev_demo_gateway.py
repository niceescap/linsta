"""Isolated, read-only demo entry point. Never used by the existing DEV server.

Bearer link is exchanged for a Secure/HttpOnly cookie; every request rechecks
the revocable, expiring grant. No account or administrator session is used.
"""
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from http.cookies import SimpleCookie
from pathlib import Path

STATE = Path('/home/jean/digger-dev/instance/dev/mobile-demo/grant.json')
COOKIE = '__Host-digger-demo'
SAFE = re.compile(r'(?:/|/decouvrir|/artistes|/a-propos|/morceaux/[0-9]+|'
    r'/preview-radio/now-playing|/preview-radio/hls/(?:stream\.m3u8|seg_[0-9]+\.ts)|'
    r'/preview-radio/tracks/[0-9]+/cover|/static/digger-icon\.png|'
    r'/static/editorial/(?:[a-zA-Z0-9_-]+\.(?:css|js|png)|'
    r'fonts/[a-zA-Z0-9_.-]+|vendor/hls\.min\.js))\Z')


class DemoGateway:
    def __init__(self, app, state=STATE, clock=time.time):
        self.app, self.state, self.clock = app, state, clock

    def __call__(self, env, start):
        def secure_start(status, headers, exc_info=None):
            # No inherited Flask session, redirects, caching, or referrer leaks.
            headers = [(k,v) for k,v in headers if k.lower() not in
                       ('set-cookie','cache-control','referrer-policy')]
            headers += [('Cache-Control','no-store, private'),
                        ('Referrer-Policy','no-referrer'),
                        ('X-Content-Type-Options','nosniff'),
                        ('X-Frame-Options','DENY'),
                        ('X-Robots-Tag','noindex, nofollow, noarchive')]
            return start(status, headers, exc_info)

        def reply(status, body, cookie=None, mime='text/plain; charset=utf-8'):
            headers = [('Content-Type',mime),('Cache-Control','no-store'),
                       ('Referrer-Policy','no-referrer'),('X-Frame-Options','DENY'),
                       ('X-Content-Type-Options','nosniff'),
                       ('X-Robots-Tag','noindex, nofollow, noarchive')]
            if cookie: headers.append(('Set-Cookie',cookie))
            start(status,headers)
            return [body.encode()]

        try:
            grant = json.loads(self.state.read_text())
            remaining = math.ceil(grant['expires'] - self.clock())
            host = grant['host']
            if not grant.get('enabled') or remaining <= 0:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            return reply('410 Gone','Cette démonstration est terminée.')
        if env.get('HTTP_HOST') != host:
            return reply('403 Forbidden','Hôte refusé.')
        path, method = env.get('PATH_INFO',''), env.get('REQUEST_METHOD','')
        if path == '/demo-access' and method == 'POST':
            try:
                length = int(env.get('CONTENT_LENGTH','0'))
                if not 1 <= length <= 128 or env.get('HTTP_ORIGIN') != 'https://' + host:
                    raise ValueError()
                token = env['wsgi.input'].read(length).decode('ascii')
                if not re.fullmatch('[A-Za-z0-9_-]{43}',token): raise ValueError()
                if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),grant['digest']):
                    raise ValueError()
            except (ValueError, UnicodeError):
                return reply('403 Forbidden','Lien invalide.')
            return reply('204 No Content','',
                f'{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={remaining}')
        if method not in ('GET','HEAD') or not SAFE.fullmatch(path):
            return reply('403 Forbidden','Accès indisponible pendant cette démonstration.')
        try:
            cookies = SimpleCookie(env.get('HTTP_COOKIE',''))
            token = cookies[COOKIE].value
            authorized = hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),grant['digest'])
        except (KeyError, ValueError):
            authorized = False
        if not authorized:
            if path != '/': return reply('401 Unauthorized','Lien privé requis.')
            # Fragment never reaches Cloudflare, server URLs, logs or referrers.
            return reply('200 OK','''<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Digger Radio — accès privé</title>
<body style="background:#121110;color:#F2EAE0;font:18px system-ui;padding:3rem"><h1>Digger Radio</h1>
<p id="state">Ouverture de la démonstration privée…</p><script>
const token=location.hash.slice(1);history.replaceState(null,'','/');
if(token)fetch('/demo-access',{method:'POST',body:token,credentials:'same-origin'})
.then(r=>{if(!r.ok)throw Error();location.replace('/')})
.catch(()=>document.getElementById('state').textContent='Lien invalide ou expiré.');
else document.getElementById('state').textContent='Ouvrez le lien privé complet reçu pour cette démonstration.';
</script></body></html>''',mime='text/html; charset=utf-8')
        # Never pass cookies/identities supplied by the visitor into Flask.
        env = dict(env, HTTP_HOST='127.0.0.1:17010', HTTP_COOKIE='',
                   DIGGER_DEMO_VERIFIED=True, **{'wsgi.url_scheme':'https'})
        return self.app(env,secure_start)


def create_application():
    from dev_web import application as app
    from flask import g, request

    @app.before_request
    def demo_listener():
        if request.environ.get('DIGGER_DEMO_VERIFIED'):
            # Capability-scoped guest, never persisted and never an account.
            g.current_user = {'id':None,'role':'demo','display_name':'Invité DEV'}

    @app.context_processor
    def demo_context():
        return {'demo_mode':bool(request.environ.get('DIGGER_DEMO_VERIFIED'))}

    return DemoGateway(app)


# Gunicorn factory: dev_demo_gateway:create_application()
