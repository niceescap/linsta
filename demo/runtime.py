"""Independent data/session configuration; never load dotenv or historical config.

Preview OAuth is optional and accepts only explicitly enabled, Digger-specific
credentials. Generic Google credentials from another service are never used.
"""
import json
import os
from pathlib import Path
import stat
import sys
import types

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'instance' / 'demo'
DATABASE = ROOT / 'digger.sqlite'


def check_paths(*, data_root=None):
    root = Path(data_root) if data_root is not None else ROOT
    if not root.is_absolute():
        raise RuntimeError('Data root must be absolute')
    database = root / 'digger.sqlite' if data_root is not None else DATABASE
    # Demo defaults stay fixed. Full publication supplies an explicit existing
    # root, so a release checkout never redirects writes to a fresh database.
    for path in (root, root / 'storage', root / 'storage' / 'tracks',
                 root / 'storage' / 'meta', root / 'storage' / 'hls', database):
        for component in (path, *path.parents):
            if component.is_symlink():
                raise RuntimeError('Demo paths must not contain symlinks')
        if path.exists():
            st = path.stat()
            if st.st_uid != os.getuid():
                raise RuntimeError('Demo data must belong to the current user')
            if path == database and (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1):
                raise RuntimeError('Demo database must be a private regular file')


def install_config(*, full_application=False, data_root=None):
    if 'config' in sys.modules or 'app' in sys.modules:
        raise RuntimeError('Demo must run in a dedicated Python process')
    check_paths(data_root=data_root)
    root = Path(data_root) if data_root is not None else ROOT
    database = root / 'digger.sqlite' if data_root is not None else DATABASE
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
    if not full_application:
        for name in tuple(os.environ):
            if name.startswith(('DIGGER_AUDIO_', 'DIGGER_COVER_', 'DIGGER_DURATION_')) or name in (
                    'YOUTUBE_API_KEY', 'SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'SPOTIFY_MARKET'):
                os.environ.pop(name)
        os.environ['SPOTIFY_METADATA_MODE'] = 'disabled'
    config = types.ModuleType('config')
    settings = dict(SECRET_KEY=secret, FLASK_HOST='127.0.0.1', FLASK_PORT=17000,
                    FLASK_DEBUG=False, DB_PATH=str(database),
                    STORAGE_PATH=str(root / 'storage'),
                    TRACKS_PATH=str(root / 'storage' / 'tracks'),
                    META_PATH=str(root / 'storage' / 'meta'),
                    HLS_PATH=str(root / 'storage' / 'hls'),
                    APP_BASE_URL='https://dig.hqwi.re', SESSION_LIFETIME_DAYS=1,
                    GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='',
                    SMTP_HOST='127.0.0.1', SMTP_PORT=1, SMTP_USER='', SMTP_PASSWORD='',
                    SMTP_FROM='demo@invalid.test', VERIFICATION_TOKEN_MAX_AGE=86400)
    if full_application:
        # No fallback to GOOGLE_CLIENT_* or another application's .env. Future
        # Digger credentials require a separate, explicit operator opt-in.
        if os.environ.get('DIGGER_GOOGLE_OAUTH_ENABLED', '0') == '1':
            client_id = os.environ.get('DIGGER_GOOGLE_CLIENT_ID', '').strip()
            client_secret = os.environ.get('DIGGER_GOOGLE_CLIENT_SECRET', '').strip()
            if client_id and client_secret:
                settings.update(GOOGLE_CLIENT_ID=client_id, GOOGLE_CLIENT_SECRET=client_secret)
    config.__dict__.update(settings)
    sys.modules['config'] = config
    return config


def require_initialized(*, data_root=None):
    """Read-only ledger verification; startup never initializes or migrates."""
    import sqlite3
    from db import _migration_inventory
    check_paths(data_root=data_root)
    database = Path(data_root) / 'digger.sqlite' if data_root is not None else DATABASE
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as conn:
        applied = conn.execute('SELECT version,name,checksum FROM schema_migrations ORDER BY version').fetchall()
    expected = [(version, name, checksum) for version, (name, checksum, _)
                in _migration_inventory(REPO / 'migrations').items()]
    if applied != expected:
        raise RuntimeError('Demo schema is not current; explicit maintenance required')
