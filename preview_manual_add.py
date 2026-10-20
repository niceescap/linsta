"""Direct admin additions to the private preview catalogue.

Uploads stay in owned temporary files until FFmpeg has probed and decoded them.
Only generated basenames enter SQLite; no browser filename becomes a path.
"""
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import uuid

from audio_storage import MediaLimits, StorageError, StorageSession, checked_open, identity
from cover_storage import prepare_cover
import preview_editorial
from private_radio import safe_root


class DuplicateTrack(ValueError):
    pass


def _validate_fields(fields):
    def text(name, maximum, *, required=False, multiline=False):
        value = fields.get(name, '')
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f'Champ {name} invalide.')
        if any((ord(char) < 32 and not (multiline and char in '\r\n\t')) or ord(char) == 127
               for char in value):
            raise ValueError(f'Champ {name} invalide.')
        value = value.strip()
        if required and not value:
            raise ValueError('Titre et artiste obligatoires.')
        return value or None

    title = text('title', 256, required=True)
    artist = text('artist', 256, required=True)
    genre = text('genre', 256) or ''
    raw_year = text('year', 4)
    if raw_year is None:
        year = None
    elif raw_year.isdigit() and 1000 <= int(raw_year) <= 9999:
        year = int(raw_year)
    else:
        raise ValueError('Année invalide (1000–9999).')
    platform = text('source_platform', 32)
    if platform not in (None, *preview_editorial.PLATFORMS):
        raise ValueError('Plateforme source invalide.')
    source_url = text('source_url', 2048)
    if source_url:
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(source_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError
            parsed.port
        except ValueError:
            raise ValueError('URL source HTTP ou HTTPS invalide.') from None
    note = text('internal_note', 2000, multiline=True)
    state = fields.get('initial_status')
    if state not in ('active', 'removed'):
        raise ValueError('Statut initial invalide.')
    # This acknowledges the existing private PREVIEW test-use gate only. It is
    # separate from, and does not grant, any public broadcast or legal approval.
    if fields.get('private_preview_confirmed') != 'on':
        raise ValueError('Confirmez l’autorisation pour le test PREVIEW privé.')
    return dict(title=title, artist=artist, genre=genre, year=year,
                source_platform=platform, source_url=source_url, internal_note=note,
                state=state)


def _probe(path, *, decode=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StorageError('invalid_audio', 'Le fichier audio doit être un fichier régulier isolé.')
        input_path = f'/proc/self/fd/{fd}'
        common = dict(capture_output=True, text=True, timeout=120, check=False,
                      pass_fds=(fd,), env={'PATH': os.defpath, 'LC_ALL': 'C'})
        probed = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
            'format=format_name,duration', '-of', 'json', input_path], **common)
        if probed.returncode:
            raise ValueError('FFprobe n’a pas pu lire le fichier audio.')
        info = json.loads(probed.stdout)['format']
        formats = set(info.get('format_name', '').split(','))
        if 'mp3' in formats:
            extension, mime = '.mp3', 'audio/mpeg'
        elif 'wav' in formats:
            extension, mime = '.wav', 'audio/wav'
        else:
            raise ValueError('Seuls les fichiers MP3 et WAV sont acceptés.')
        duration = float(info.get('duration', 'nan'))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Durée audio impossible à déterminer.')
        if decode:
            result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-xerror',
                '-nostdin', '-y', '-i', input_path, '-map', '0:a:0', '-vn', '-sn', '-dn',
                '-threads', '1', '-f', 'null', '-'], **common)
            if result.returncode:
                raise ValueError('FFmpeg n’a pas pu décoder entièrement le fichier audio.')
        if (identity(before) != identity(os.fstat(fd)) or before.st_nlink != 1
                or identity(before) != identity(os.stat(path, follow_symlinks=False))):
            raise StorageError('media_integrity', 'Le fichier audio a changé pendant la validation.')
        return extension, mime, duration
    except subprocess.TimeoutExpired as error:
        raise ValueError('La vérification du fichier audio a dépassé le délai autorisé.') from error
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Fichier audio illisible ou invalide.') from error
    finally:
        os.close(fd)


def prepare_audio(session, stream, limits):
    prepared = session.receive(stream, 'upload', limits.audio_bytes)
    extension, mime, duration = _probe(prepared.path, decode=True)
    if duration > limits.duration_seconds:
        raise StorageError('audio_too_long', 'La durée audio dépasse la limite autorisée.')
    verified = session.describe(prepared.path, 'upload', duration=duration, mime=mime)
    return verified, extension


def _publish_name(prefix, extension):
    return f'{prefix}-{uuid.uuid4().hex}{extension}'


def add_track(root, fields, audio_stream, cover_stream=None, *, limits=None):
    root = safe_root(Path(root))
    fields = _validate_fields(fields)
    limits = limits or MediaLimits(audio_bytes=10*1024*1024, cover_bytes=4*1024*1024,
                                   duration_seconds=600, cover_pixels=4_000_000,
                                   cover_dimension=4096)
    session = StorageSession(root, root/'covers')
    keep = False
    try:
        audio, audio_extension = prepare_audio(session, audio_stream, limits)
        cover = None
        if cover_stream is not None:
            _, cover = prepare_cover(session, cover_stream.stream, cover_stream.filename, limits)
        audio_name = _publish_name('digger-manual', audio_extension)
        cover_name = _publish_name('digger-cover', '.png' if cover and cover.mime == 'image/png' else '.jpg')
        relative_cover = f'covers/{cover_name}' if cover else None
        conn = preview_editorial.connect(root, write=True)
        try:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM private_tracks WHERE sha256=? LIMIT 1', (audio.sha256,)).fetchone():
                raise DuplicateTrack('Ce fichier audio est déjà présent dans le catalogue.')
            position = conn.execute('SELECT COALESCE(MAX(position),-1)+1 FROM private_tracks').fetchone()[0]
            session.publish(audio, root, audio_name)
            if cover:
                session.publish(cover, root/'covers', cover_name)
            enabled = int(fields['state'] == 'active')
            conn.execute('''INSERT INTO private_tracks
                (position,filename,title,artist,sha256,size,duration,private_rights_confirmed,
                 enabled,removed_at,genre,year,cover_path,source_platform,source_url,internal_note,updated_at)
                VALUES (?,?,?,?,?,?,?,1,?,CASE WHEN ?=0 THEN CURRENT_TIMESTAMP ELSE NULL END,
                        ?,?,?,?,?,?,CURRENT_TIMESTAMP)''',
                (position,audio_name,fields['title'],fields['artist'],audio.sha256,audio.size,audio.duration,
                 enabled,enabled,fields['genre'],fields['year'],relative_cover,fields['source_platform'],
                 fields['source_url'],fields['internal_note']))
            row = dict(conn.execute('SELECT * FROM private_tracks WHERE id=last_insert_rowid()').fetchone())
            conn.execute('''INSERT INTO private_catalogue_events(track_id,action,before_json,after_json)
                            VALUES (?,'manual_admin_add','{}',?)''',
                         (row['id'], json.dumps(row, ensure_ascii=False, sort_keys=True)))
            conn.commit()
            keep = True
            return row
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    finally:
        session.cleanup(keep_published=keep)


def prepare_cover_replacement(root, upload, *, limits=None):
    if upload is None or not upload.filename:
        return None, None
    root = safe_root(Path(root))
    limits = limits or MediaLimits(audio_bytes=10*1024*1024, cover_bytes=4*1024*1024,
                                   duration_seconds=600, cover_pixels=4_000_000,
                                   cover_dimension=4096)
    session = StorageSession(root, root/'covers')
    try:
        _, prepared = prepare_cover(session, upload.stream, upload.filename, limits)
        extension = '.png' if prepared.mime == 'image/png' else '.jpg'
        name = _publish_name('digger-cover', extension)
        session.publish(prepared, root/'covers', name)
        return f'covers/{name}', session
    except BaseException:
        session.cleanup()
        raise


def finish_cover_replacement(session, *, keep):
    if session is not None:
        session.cleanup(keep_published=keep)
