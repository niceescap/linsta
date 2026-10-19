"""Full Digger contribution app, using existing data; public radio stays closed.

Dedicated Gunicorn entry point. No demo template override, migrations, worker,
network call or service operation occurs during import. Operator credentials are
loaded by runtime configuration without printing their values.
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
from flask import jsonify, request

# Keep the existing session secret, cookie name, users and files. Limits match
# the existing Nginx 16 MiB request ceiling; no infrastructure change is needed.
application.config.update(SESSION_COOKIE_NAME='digger_demo_session',
                          MAX_CONTENT_LENGTH=16 * 1024 * 1024)
application.config['MEDIA_LIMITS'] = MediaLimits(audio_bytes=10 * 1024 * 1024,
    cover_bytes=4 * 1024 * 1024, duration_seconds=600, cover_pixels=4_000_000,
    cover_dimension=4096)


@application.before_request
def public_media_closed():
    if request.path.startswith(('/hls/', '/media/cover/')):
        return jsonify(error='Diffusion publique indisponible.'), 404


@application.context_processor
def radio_mode():
    return {'radio_enabled': False}


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
