"""Full Digger contribution app, using existing data; public radio stays closed.

Dedicated Gunicorn entry point. No demo template override, migrations, worker,
network call or service operation occurs during import. Existing session settings
are reused, while Digger OAuth is optional and disabled for the local preview.
"""
import os
from pathlib import Path

from demo.runtime import install_config, require_initialized

data_root = os.environ.get('DIGGER_DATA_ROOT')
if not data_root or not Path(data_root).is_absolute():
    raise RuntimeError('An explicit absolute DIGGER_DATA_ROOT is required')
install_config(full_application=True, data_root=data_root)
require_initialized(data_root=data_root)

from app import app as application
from audio_storage import MediaLimits
from flask import jsonify, render_template, request
from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

# Keep the existing session secret, cookie name, users and files. Limits match
# the existing Nginx 16 MiB request ceiling; no infrastructure change is needed.
application.config.update(SESSION_COOKIE_NAME='digger_demo_session',
                          MAX_CONTENT_LENGTH=16 * 1024 * 1024)
application.config['MEDIA_LIMITS'] = MediaLimits(audio_bytes=10 * 1024 * 1024,
    cover_bytes=4 * 1024 * 1024, duration_seconds=600, cover_pixels=4_000_000,
    cover_dimension=4096)
application.config['GOOGLE_OAUTH_ENABLED'] = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


@application.before_request
def public_media_closed():
    if request.path.startswith(('/hls/', '/media/cover/')):
        return jsonify(error='Diffusion publique indisponible.'), 404
    if request.endpoint in ('login_google', 'auth_google_callback') and not application.config['GOOGLE_OAUTH_ENABLED']:
        return jsonify(error='Connexion Google indisponible pour cette preview.', code='oauth_unavailable'), 503
    if request.endpoint in ('register', 'verify'):
        return jsonify(error='Les comptes de test sont créés sur invitation.'), 403


@application.context_processor
def radio_mode():
    return {'radio_enabled': False, 'local_login_enabled': True,
            'google_oauth_enabled': application.config['GOOGLE_OAUTH_ENABLED']}


@application.get('/login/local')
def local_login():
    return render_template('local_login.html')


def closed_now_playing():
    return jsonify(track_id=None, message='Le direct n’est pas encore ouvert.')


application.view_functions['now_playing'] = closed_now_playing


@application.after_request
def private_dynamic_responses(response):
    if request.endpoint != 'static':
        response.headers['Cache-Control'] = 'no-store'
        response.vary.add('Cookie')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
