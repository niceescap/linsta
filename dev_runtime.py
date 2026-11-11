"""Offline-only dev configuration. No production configuration imports."""
import os
from pathlib import Path
import secrets
import stat
import sys
import types

ROOT = Path('/home/jean/digger-dev/instance/dev')
PORT = 17010


def check_paths():
    if not ROOT.is_absolute():
        raise RuntimeError('Absolute dev root required')
    supplied = os.environ.get('DIGGER_DATA_ROOT')
    if supplied is not None and supplied != str(ROOT):
        raise RuntimeError('DIGGER_DATA_ROOT must equal the dev root')
    for item in (ROOT, *ROOT.parents):
        if item.is_symlink():
            raise RuntimeError('Dev root cannot contain symlinks')
    if ROOT.exists():
        for directory, dirs, names in os.walk(ROOT, followlinks=False):
            for path in [Path(directory), *(Path(directory)/n for n in dirs+names)]:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
                    raise RuntimeError('Dev data must be owned and contain no symlinks')
                if stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1 or info.st_mode & 0o077:
                        raise RuntimeError('Dev files must be private and single-link')
                elif not stat.S_ISDIR(info.st_mode):
                    raise RuntimeError('Unsupported dev data entry')
                if info.st_mode & 0o077:
                    raise RuntimeError('Dev directories must be private')


def prepare():
    """Explicit fresh synthetic fixture preparation; never invoked at import."""
    check_paths()
    if ROOT.exists():
        raise RuntimeError('Refusing to overwrite existing dev data')
    old = os.umask(0o077)
    try:
        ROOT.mkdir(parents=True, mode=0o700)
        for name in ('storage/tracks', 'storage/meta', 'storage/hls', 'fixtures'):
            (ROOT/name).mkdir(parents=True, mode=0o700)
        (ROOT/'session.secret').write_text(secrets.token_hex(48))
        from db import init_db
        init_db(ROOT/'digger.sqlite')
        import private_radio
        private_radio.initialize(ROOT/'private-radio')
        # Locally generated silence, never automatically admitted for broadcast.
        import wave
        with wave.open(str(ROOT/'fixtures/synthetic-silence.wav'), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(44100)
            output.writeframes(b'\0\0' * 44100)
    finally:
        os.umask(old)
    check_paths()


def install_config():
    if 'config' in sys.modules or 'app' in sys.modules:
        raise RuntimeError('Dev requires a fresh dedicated Python process')
    check_paths()
    secret = (ROOT/'session.secret').read_text()
    if len(secret) < 64:
        raise RuntimeError('Run explicit dev preparation first')
    # Discard inherited integrations; never load dotenv, beta settings or OAuth.
    for name in tuple(os.environ):
        if name.startswith(('DIGGER_', 'SPOTIFY_', 'YOUTUBE_', 'GOOGLE_', 'SMTP_')):
            os.environ.pop(name)
    os.environ['SPOTIFY_METADATA_MODE'] = 'disabled'
    module = types.ModuleType('config')
    module.__dict__.update(SECRET_KEY=secret, FLASK_HOST='127.0.0.1', FLASK_PORT=PORT,
        FLASK_DEBUG=False, DB_PATH=str(ROOT/'digger.sqlite'),
        STORAGE_PATH=str(ROOT/'storage'), TRACKS_PATH=str(ROOT/'storage/tracks'),
        META_PATH=str(ROOT/'storage/meta'), HLS_PATH=str(ROOT/'storage/hls'),
        APP_BASE_URL=f'http://127.0.0.1:{PORT}', SESSION_LIFETIME_DAYS=1,
        GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='', SMTP_HOST='127.0.0.1',
        SMTP_PORT=1, SMTP_USER='', SMTP_PASSWORD='', SMTP_FROM='dev@invalid.test',
        VERIFICATION_TOKEN_MAX_AGE=86400)
    sys.modules['config'] = module
    from demo.runtime import require_initialized
    require_initialized(data_root=ROOT)


class DevController:
    """Read-only local engine state; never controls systemd or a supplied PID."""
    def inspect(self):
        import fcntl
        import json
        check_paths()
        root = ROOT/'private-radio'
        fd = None
        try:
            fd = os.open(root/'radio.lock', os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return {'state': 'inactive', 'invocation_id': ''}
            except BlockingIOError:
                marker = json.loads((root/'preview-run.json').read_text())
                if marker.get('state') == 'running':
                    return {'state': 'active', 'invocation_id': marker.get('invocation_id', '')}
        except (OSError, ValueError):
            pass
        finally:
            if fd is not None:
                os.close(fd)
        return {'state': 'unavailable', 'invocation_id': ''}

    def action(self, action):
        from preview_radio_web import PreviewError
        raise PreviewError('Use the foreground dev launcher; web service actions are disabled.')
