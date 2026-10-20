"""Session-protected bridge to the separate private catalogue and bounded engine."""
from contextlib import contextmanager
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import preview_jingles
import preview_editorial
import subprocess
import time
import warnings

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, send_file, url_for)
from mutagen.id3 import ID3, error as ID3Error
from PIL import Image, ImageOps

from audio_storage import StorageError, checked_open
from private_radio import safe_root, SEGMENT

UNIT = 'digger-preview-radio.service'
DEFAULT_ROOT = '/home/jean/digger/instance/demo/private-radio'


class PreviewError(ValueError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class ServiceController:
    """Only this fixed user unit is addressable; no shell or request-derived unit."""
    def inspect(self):
        try:
            result = subprocess.run(['systemctl', '--user', 'show', UNIT,
                '--property=ActiveState,SubState,InvocationID,LoadState'],
                capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {'state': 'unavailable', 'invocation_id': ''}
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode or fields.get('LoadState') != 'loaded':
            return {'state': 'unavailable', 'invocation_id': ''}
        return {'state': fields.get('ActiveState', 'unavailable'),
                'invocation_id': fields.get('InvocationID', '')}

    def action(self, action):
        if action not in ('start', 'stop'):
            raise PreviewError('Action inconnue.', 400)
        state = self.inspect()['state']
        if action == 'start' and state in ('active', 'activating', 'deactivating'):
            raise PreviewError('Une session PREVIEW est déjà en cours ou en transition.', 409)
        if state == 'unavailable':
            raise PreviewError('Le service PREVIEW n’est pas installé ou est indisponible.')
        try:
            subprocess.run(['systemctl', '--user', action, UNIT], capture_output=True,
                           text=True, check=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            raise PreviewError('La commande PREVIEW n’a pas abouti. Vérifiez son état.') from None


@contextmanager
def catalogue(root):
    root = safe_root(root)
    # Never call connect()/migrate() from the web: this connection cannot write.
    conn = sqlite3.connect((root / 'catalogue.sqlite').as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def track(root, tid):
    with catalogue(root) as conn:
        row = conn.execute('SELECT * FROM private_tracks WHERE id=?', (tid,)).fetchone()
    if row is None or not row['private_rights_confirmed']:
        raise PreviewError('Titre privé introuvable.', 404)
    return dict(row)


def audio_file(root, row):
    # A regular, unchanged MP3 attached to this catalogue; never a user path.
    if not row['filename'].lower().endswith('.mp3'):
        raise PreviewError('Fichier audio invalide.', 404)
    stream, _ = checked_open(root, row['filename'], sha256=row['sha256'], size=row['size'])
    return stream


def read_small(root, name, limit):
    stream, snapshot = checked_open(root, name)
    with stream:
        if snapshot[2] > limit:
            raise PreviewError('État PREVIEW invalide.')
        return stream.read(), snapshot


def health(root, controller, *, now=None):
    now = time.time() if now is None else now
    service = controller.inspect()
    result = {'status': service['state'], 'available': False, 'message': 'Radio PREVIEW arrêtée.'}
    if service['state'] not in ('active', 'activating'):
        if service['state'] == 'unavailable': result['message'] = 'Service PREVIEW indisponible.'
        if service['state'] == 'failed': result['message'] = 'Radio PREVIEW en erreur.'
        return result
    result.update(status='starting', message='Radio PREVIEW en démarrage…')
    try:
        marker = json.loads(read_small(root, 'preview-run.json', 4096)[0])
        if not isinstance(marker, dict):
            raise ValueError('Invalid run marker')
        started = marker.get('started_at')
        if (not re.fullmatch('[0-9a-f]{32}', service['invocation_id'])
                or marker.get('invocation_id') != service['invocation_id']
                or marker.get('state') != 'running' or type(started) not in (int, float)
                or not math.isfinite(started) or started > now):
            return result
        playlist, snapshot = read_small(root / 'hls', 'stream.m3u8', 65536)
        text = playlist.decode('utf-8')
        segments = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
        if not text.startswith('#EXTM3U') or not segments or any(not SEGMENT.fullmatch(s) for s in segments):
            raise ValueError('Invalid playlist')
        _, last = read_small(root / 'hls', segments[-1], 2 * 1024 * 1024)
        playlist_mtime, segment_mtime = snapshot[3] / 1e9, last[3] / 1e9
        if (min(playlist_mtime, segment_mtime) < started or last[2] == 0
                or max(playlist_mtime, segment_mtime) > now + 1
                or now - min(playlist_mtime, segment_mtime) > 20):
            raise ValueError('Stale playlist')
        return dict(result, status='running', available=True, message='Radio PREVIEW / TEST en cours.',
                    started_at=started, playlist=playlist, segments=segments)
    except (OSError, ValueError, KeyError, TypeError, StorageError):
        # Includes stale files from any prior run; never publish them as a live.
        result.update(status='waiting', message='Flux PREVIEW en attente de progression HLS.')
        return result


def create_preview_blueprint(login_required, admin_required):
    bp = Blueprint('preview_radio', __name__)

    def root():
        return Path(current_app.config['PREVIEW_RADIO_ROOT'])

    def controller():
        return current_app.config['PREVIEW_RADIO_CONTROLLER']

    @bp.errorhandler(PreviewError)
    def business_error(error):
        return jsonify(error=str(error)), error.status

    @bp.errorhandler(StorageError)
    def media_error(error):
        return jsonify(error='Média PREVIEW absent ou modifié.'), 404

    @bp.errorhandler(sqlite3.Error)
    def database_error(error):
        return jsonify(error='Catalogue PREVIEW indisponible.'), 503

    @bp.after_request
    def privacy(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.vary.add('Cookie')
        return response

    @bp.get('/admin/preview-radio')
    @admin_required
    def index():
        query, selected = request.args.get('q', ''), request.args.get('status', 'all')
        try:
            rows = preview_editorial.listing(root(), query=query, state=selected)
        except ValueError as error:
            raise PreviewError(str(error), 400)
        state = health(root(), controller())
        return render_template('preview_radio.html', tracks=rows, state=state, jingles=preview_jingles.scan(root()),
                               editorial_status=preview_editorial.status, reasons=preview_editorial.REASONS,
                               query=query, selected=selected)

    @bp.get('/admin/preview-radio/tracks/<int:track_id>/confirm')
    @admin_required
    def confirm_edit(track_id):
        action = request.args.get('action')
        if action not in ('remove', 'reject'):
            raise PreviewError('Action invalide.', 400)
        return render_template('preview_radio_confirm.html', track=track(root(),track_id), action=action,
                               reasons=preview_editorial.REASONS)

    @bp.post('/admin/preview-radio/tracks/<int:track_id>/edit')
    @admin_required
    def edit_track(track_id):
        try:
            preview_editorial.change(root(),track_id,int(request.form.get('revision','')),
                request.form.get('action'),title=request.form.get('title'),artist=request.form.get('artist'),
                genre=request.form.get('genre',''),year=request.form.get('year'),
                reason=request.form.get('reason'),confirm=request.form.get('confirm'))
        except (ValueError, sqlite3.OperationalError) as error:
            raise PreviewError(str(error),409)
        return redirect(url_for('.index'),code=303)

    @bp.post('/admin/preview-radio/start')
    @admin_required
    def start():
        controller().action('start')
        return redirect(url_for('.index'), code=303)

    @bp.post('/admin/preview-radio/stop')
    @admin_required
    def stop():
        controller().action('stop')
        return redirect(url_for('.index'), code=303)

    @bp.get('/preview-radio/tracks/<int:track_id>/audio')
    @admin_required
    def audio(track_id):
        row = track(root(), track_id)
        stream = audio_file(root(), row)
        try:
            response = send_file(stream, mimetype='audio/mpeg', download_name='preview.mp3',
                                 conditional=False, etag=False, max_age=0)
            response.content_length = row['size']
            response.make_conditional(request.environ, accept_ranges=True, complete_length=row['size'])
            response.call_on_close(stream.close)
            return response
        except BaseException:
            stream.close()
            raise

    @bp.get('/preview-radio/tracks/<int:track_id>/cover')
    @login_required
    def cover(track_id):
        row = track(root(), track_id)
        with audio_file(root(), row) as stream:
            try:
                pictures = ID3(stream).getall('APIC')
            except ID3Error:
                pictures = []
        if pictures:
            raw = pictures[0].data
            if len(raw) <= 8 * 1024 * 1024:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('error', Image.DecompressionBombWarning)
                        with Image.open(io.BytesIO(raw), formats=('JPEG', 'PNG')) as image:
                            if image.width > 4096 or image.height > 4096 or image.width * image.height > 4_000_000:
                                raise ValueError('Excessive cover')
                            image.load()
                            display = ImageOps.exif_transpose(image).convert('RGB')
                            output = io.BytesIO()
                            display.save(output, format='JPEG')
                            display.close()
                    output.seek(0)
                    return send_file(output, mimetype='image/jpeg', etag=False, max_age=0)
                except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
                    pass
        return redirect(url_for('static', filename='digger-icon.png'))

    @bp.get('/preview-radio/now-playing')
    @login_required
    def now_playing():
        state = health(root(), controller())
        result = {k: state[k] for k in ('status', 'available', 'message')}
        result['track_id'] = None
        result['type'] = None
        result['timing_available'] = False
        result.update(next_track_id=None, next_title=None, next_artist=None, next_cover_url=None)
        if state['available']:
            try:
                data = json.loads(read_small(root() / 'hls', 'now.json', 65536)[0])
                played_at = request.args.get('at')
                timing = None
                if played_at is not None:
                    # Resolve the audible position, not the encoder's newer current track.
                    at = float(played_at)
                    if not math.isfinite(at) or not state['started_at'] - 5 <= at <= time.time() + 5:
                        raise ValueError('Invalid playback date')
                    timing = json.loads(read_small(root() / 'hls', 'timeline.json', 65536)[0])
                    origin = timing['origin']
                    if (not isinstance(origin, (int, float)) or not math.isfinite(origin)
                            or timing['started_at'] < state['started_at']):
                        raise ValueError('Timing from an old run')
                    audible = [item for item in timing['tracks'] if origin + item['offset'] <= at]
                    if not audible:
                        raise ValueError('Playback timing not yet available')
                    item = audible[-1]
                    data = dict(track_id=item['id'], next_track_id=item['next_track_id'],
                                started_at=origin + item['offset'], type=item.get('type', 'track'),
                                duration=item.get('duration'))
                if (not isinstance(data, dict) or type(data.get('started_at')) not in (int, float)
                        or not math.isfinite(data['started_at']) or data['started_at'] < state['started_at']):
                    raise ValueError('Invalid item timing')
                if data.get('type') == 'jingle' and data.get('track_id') is None:
                    duration = data.get('duration')
                    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 1800:
                        raise ValueError('Invalid jingle duration')
                    result.update(type='jingle', title='Digger Radio', artist='Station ID', duration=duration,
                                  cover_url=url_for('static', filename='digger-icon.png'))
                elif type(data.get('track_id')) is int:
                    row = track(root(), data['track_id'])
                    # A withdrawn current item finishes normally; only future selection is filtered.
                    result.update(type='track', track_id=row['id'], title=row['title'], artist=row['artist'],
                                  duration=row['duration'], cover_url=url_for('.cover', track_id=row['id']))
                else:
                    raise ValueError('No playable item')
                result['elapsed'] = max(0, (at if timing is not None else time.time()) - data['started_at'])
                if timing is not None:
                    result.update(timing_available=True, playback_started_at=data['started_at'])
                if type(data.get('next_track_id')) is int:
                    try:
                        following = track(root(), data['next_track_id'])
                        if preview_editorial.eligible(following):
                            result.update(next_track_id=following['id'], next_title=following['title'],
                                          next_artist=following['artist'],
                                          next_cover_url=url_for('.cover', track_id=following['id']))
                    except (PreviewError, sqlite3.Error):
                        pass
            except (OSError, ValueError, TypeError, KeyError, StorageError):
                pass
        return jsonify(result)

    @bp.get('/preview-radio/hls/<name>')
    @login_required
    def hls(name):
        if name != 'stream.m3u8' and not SEGMENT.fullmatch(name):
            raise PreviewError('Fichier PREVIEW introuvable.', 404)
        state = health(root(), controller())
        if not state['available']:
            raise PreviewError(state['message'])
        if name == 'stream.m3u8':
            return current_app.response_class(state['playlist'], mimetype='application/vnd.apple.mpegurl')
        if name not in state['segments']:
            raise PreviewError('Segment hors de la playlist PREVIEW.', 404)
        data, snapshot = read_small(root() / 'hls', name, 2 * 1024 * 1024)
        if snapshot[3] / 1e9 < state['started_at'] or not data:
            raise PreviewError('Segment périmé.', 404)
        return current_app.response_class(data, mimetype='video/mp2t')

    return bp
