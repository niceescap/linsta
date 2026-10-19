"""Independent configuration. Never import config.py or load a dotenv file."""
import json
import os
from pathlib import Path
import stat
import sys
import types

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'instance' / 'demo'
DATABASE = ROOT / 'digger.sqlite'


def check_paths():
    # Fixed roots, with no configurable redirection to historical data.
    for path in (ROOT, ROOT / 'storage', ROOT / 'storage' / 'tracks',
                 ROOT / 'storage' / 'meta', ROOT / 'storage' / 'hls', DATABASE):
        for component in (path, *path.parents):
            if component.is_symlink():
                raise RuntimeError('Demo paths must not contain symlinks')
        if path.exists():
            st = path.stat()
            if st.st_uid != os.getuid():
                raise RuntimeError('Demo data must belong to the current user')
            if path == DATABASE and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
                raise RuntimeError('Demo database must be a private regular file')


def install_config():
    if 'config' in sys.modules or 'app' in sys.modules:
        raise RuntimeError('Demo must run in a dedicated Python process')
    check_paths()
    path = Path(os.environ.get('DIGGER_DEMO_SETTINGS',
                              '/home/jean/.config/digger-demo/settings.json'))
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError('Settings must be an absolute, non-symlink path outside Git')
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(REPO):
        raise RuntimeError('Settings must be outside the repository')
    with path.open() as stream:
        st = os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
                or st.st_mode & 0o077 or st.st_nlink != 1):
            raise RuntimeError('Settings must be owned by the user, mode 0600, single link')
        values = json.load(stream)
    if set(values) != {'secret_key'}:
        raise RuntimeError('Only secret_key is supported in demo settings')
    secret = values['secret_key']
    if (not isinstance(secret, str) or len(secret) < 64
            or len(set(secret)) < 16 or 'REPLACE' in secret):
        raise RuntimeError('Provide a fresh random secret of at least 64 characters')
    # Do not inherit historical metadata credentials or resource limits.
    for name in tuple(os.environ):
        if name.startswith(('DIGGER_AUDIO_', 'DIGGER_COVER_', 'DIGGER_DURATION_')) or name in (
                'YOUTUBE_API_KEY', 'SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'SPOTIFY_MARKET'):
            os.environ.pop(name)
    os.environ['SPOTIFY_METADATA_MODE'] = 'disabled'
    config = types.ModuleType('config')
    settings = dict(SECRET_KEY=secret, FLASK_HOST='127.0.0.1', FLASK_PORT=17000,
                    FLASK_DEBUG=False, DB_PATH=str(DATABASE),
                    STORAGE_PATH=str(ROOT / 'storage'),
                    TRACKS_PATH=str(ROOT / 'storage' / 'tracks'),
                    META_PATH=str(ROOT / 'storage' / 'meta'),
                    HLS_PATH=str(ROOT / 'storage' / 'hls'),
                    APP_BASE_URL='https://dig.hqwi.re', SESSION_LIFETIME_DAYS=1,
                    GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='',
                    SMTP_HOST='127.0.0.1', SMTP_PORT=1, SMTP_USER='', SMTP_PASSWORD='',
                    SMTP_FROM='demo@invalid.test', VERIFICATION_TOKEN_MAX_AGE=86400)
    config.__dict__.update(settings)
    sys.modules['config'] = config
    return config


def require_initialized():
    """Read-only ledger verification; startup never initializes or migrates."""
    import sqlite3
    from db import _migration_inventory
    check_paths()
    with sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True) as conn:
        applied = conn.execute('SELECT version,name,checksum FROM schema_migrations ORDER BY version').fetchall()
    expected = [(version, name, checksum) for version, (name, checksum, _)
                in _migration_inventory(REPO / 'migrations').items()]
    if applied != expected:
        raise RuntimeError('Demo schema is not current; explicit maintenance required')
