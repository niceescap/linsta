"""Isolated WSGI entry point. Import does not start a server or engine."""
import dev_runtime

dev_runtime.install_config()
from app import app as application, login_required, admin_required
from flask import g, jsonify, render_template, request, url_for
from preview_radio_web import create_preview_blueprint

application.config.update(SESSION_COOKIE_NAME='digger_dev_session',
    SESSION_COOKIE_SECURE=False, GOOGLE_OAUTH_ENABLED=False,
    PREVIEW_RADIO_ROOT=str(dev_runtime.ROOT/'private-radio'),
    PREVIEW_RADIO_CONTROLLER=dev_runtime.DevController(),
    MAX_CONTENT_LENGTH=16*1024*1024, EDITORIAL_LIVE_DATA=True)
# Direct localhost only; disregard inherited reverse-proxy trust.
_local_app = application.wsgi_app.app


def _checked_local_app(environ, start_response):
    # Validate before account lookup, uploads or any other Flask hook.
    dev_runtime.check_paths()
    return _local_app(environ, start_response)


application.wsgi_app = _checked_local_app
application.register_blueprint(create_preview_blueprint(login_required, admin_required))


@application.before_request
def isolated_request():
    dev_runtime.check_paths()
    if request.host not in ('127.0.0.1:17010', 'localhost:17010'):
        return jsonify(error='Local dev host required'), 403
    if request.path.startswith(('/hls/', '/media/cover/')):
        return jsonify(error='Diffusion publique indisponible.'), 404
    if request.endpoint in ('login_google', 'auth_google_callback', 'register', 'verify'):
        return jsonify(error='External integrations and registration disabled in dev'), 403


@application.get('/login/local')
def local_login():
    return render_template('local_login.html')


@application.context_processor
def radio_mode():
    return dict(radio_enabled=g.current_user is not None, local_login_enabled=True,
        preview_radio_enabled=True, google_oauth_enabled=False,
        radio_stream_url=url_for('preview_radio.hls', name='stream.m3u8'),
        radio_metadata_url=url_for('preview_radio.now_playing'))


application.view_functions['now_playing'] = lambda: jsonify(track_id=None)


@application.after_request
def private_responses(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.vary.add('Cookie')
    return response


# Editorial interface is installed only in the isolated DEV entry point.
from editorial_web import install as install_editorial
install_editorial(application, login_required)
