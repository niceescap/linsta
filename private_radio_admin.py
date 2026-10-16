"""SSH-only catalogue administration. Never register this app on the public web app."""
import argparse
import hmac
import os
import secrets
import signal
import sqlite3
import stat
import time
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request
from werkzeug.serving import WSGIRequestHandler, make_server

import private_radio as radio

PORT = 17002
ORIGINS = {f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'}


def migrate(root):
    """Additive, transactional and idempotent; playback must be stopped initially."""
    conn = radio.connect(root)
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 1:
            return
        if version != 0:
            raise ValueError('Version de catalogue inconnue.')
        with radio.catalogue_lock(root), conn:
            conn.execute('BEGIN IMMEDIATE')
            # Recheck after acquiring the lock: another admin may have migrated.
            if conn.execute('PRAGMA user_version').fetchone()[0] == 1:
                return
            conn.execute('ALTER TABLE private_tracks ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))')
            conn.execute('ALTER TABLE private_tracks ADD COLUMN removed_at TEXT')
            conn.execute('ALTER TABLE private_tracks ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
            conn.execute('''CREATE TABLE private_catalogue_events (
                id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL REFERENCES private_tracks(id),
                action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            conn.execute('PRAGMA user_version=1')
    finally:
        conn.close()


def listing(root):
    conn = radio.connect(root)
    try:
        rows = [dict(r) for r in conn.execute('SELECT * FROM private_tracks ORDER BY position')]
    finally:
        conn.close()
    imported = {r['filename'] for r in rows}
    # Inventory only: no decode, import, following links or file contents served.
    extras = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name.lower().endswith('.mp3') and entry.name not in imported:
                try:
                    info = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue  # A file disappeared during this read-only inventory.
                extras.append({'name': entry.name, 'size': info.st_size,
                               'regular': stat.S_ISREG(info.st_mode) and info.st_nlink == 1})
    return rows, sorted(extras, key=lambda e: (e['name'].casefold(), e['name']))


def change(root, track_id, revision, action, *, title=None, artist=None, confirm=None):
    import json
    if type(track_id) is not int or not 0 < track_id <= 2**63 - 1:
        raise ValueError('Identifiant invalide.')
    if type(revision) is not int or revision < 0:
        raise ValueError('Révision invalide.')
    with radio.catalogue_lock(root):
        conn = radio.connect(root)
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT * FROM private_tracks WHERE id=?', (track_id,)).fetchone()
                if row is None or row['removed_at'] is not None:
                    raise ValueError('Morceau absent ou déjà retiré.')
                if row['revision'] != revision:
                    raise ValueError('Page périmée : rechargez avant de modifier.')
                values = dict(row)
                if action == 'metadata':
                    for key, value in (('title', title), ('artist', artist)):
                        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 256
                                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                            raise ValueError('Titre et artiste requis, 256 caractères maximum, sans caractères de contrôle.')
                        values[key] = value.strip()
                elif action == 'toggle':
                    values['enabled'] = 1 - row['enabled']
                elif action == 'remove':
                    if confirm != str(track_id):
                        raise ValueError('Confirmation explicite du retrait requise.')
                    values['enabled'] = 0
                else:
                    raise ValueError('Action inconnue.')
                conn.execute('''UPDATE private_tracks SET title=?,artist=?,enabled=?,
                    removed_at=CASE WHEN ?='remove' THEN CURRENT_TIMESTAMP ELSE removed_at END,
                    revision=revision+1 WHERE id=?''',
                    (values['title'], values['artist'], values['enabled'], action, track_id))
                after = dict(conn.execute('SELECT * FROM private_tracks WHERE id=?', (track_id,)).fetchone())
                conn.execute('''INSERT INTO private_catalogue_events(track_id,action,before_json,after_json)
                    VALUES (?,?,?,?)''', (track_id, action, json.dumps(dict(row)), json.dumps(after)))
        finally:
            conn.close()


def create_app(root):
    root = Path(root).absolute()
    radio.safe_root(root)
    password = (root/'access.key').read_text()
    if len(password) < 32:
        raise ValueError('Invalid private access key')
    app = Flask(__name__, static_folder=None, template_folder='private_admin_templates')
    app.config.update(MAX_CONTENT_LENGTH=8192, MAX_FORM_MEMORY_SIZE=8192, MAX_FORM_PARTS=10)
    csrf = secrets.token_urlsafe(32)  # Ephemeral, scoped to this private admin process.

    @app.before_request
    def protect():
        if request.host not in (f'127.0.0.1:{PORT}', f'localhost:{PORT}'):
            abort(400)
        auth = request.authorization
        if (auth is None or auth.type.lower() != 'basic'
                or not hmac.compare_digest((auth.username or '').encode(), radio.USERNAME.encode())
                or not hmac.compare_digest((auth.password or '').encode(), password.encode())):
            return '', 401, {'WWW-Authenticate': 'Basic realm="Digger private catalogue"'}
        if request.method == 'POST':
            if request.headers.get('Origin') not in ORIGINS | {None}:
                abort(403)
            tokens = request.form.getlist('csrf_token')
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode(), csrf.encode()):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Cross-Origin-Resource-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    @app.get('/')
    def index():
        rows, extras = listing(root)
        return render_template('catalogue.html', tracks=rows, extras=extras, csrf=csrf)

    @app.post('/tracks/<int:track_id>')
    def edit(track_id):
        try:
            revisions = request.form.getlist('revision')
            if len(revisions) != 1 or not revisions[0].isdigit() or len(revisions[0]) > 10:
                raise ValueError('Révision invalide.')
            change(root, track_id, int(revisions[0]), request.form.get('action'),
                   title=request.form.get('title'), artist=request.form.get('artist'),
                   confirm=request.form.get('confirm'))
        except ValueError as error:
            return render_template('error.html', error=str(error)), 409
        except sqlite3.OperationalError:
            return render_template('error.html', error='Catalogue indisponible : réessayez après arrêt du moteur.'), 409
        return redirect('/', code=303)

    return app


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass  # No URLs, credentials or metadata in access logs.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--migrate-only', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error('Durée entre 1 et 3600 secondes.')
    os.umask(0o077)
    migrate(radio.ROOT)
    if args.migrate_only:
        print('Private catalogue schema ready; no server started.')
        return
    app = create_app(radio.ROOT)
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    with make_server('127.0.0.1', PORT, app, request_handler=QuietHandler) as server:
        server.timeout = 1
        # Bound slow clients too; this is deliberately a single-request server.
        original_get_request = server.get_request
        def get_request():
            sock, address = original_get_request()
            sock.settimeout(3)
            return sock, address
        server.get_request = get_request
        deadline = time.monotonic() + args.seconds
        print(f'Catalogue privé sur 127.0.0.1:{PORT}, durée maximale {args.seconds}s.', flush=True)
        while not stopped and time.monotonic() < deadline:
            server.handle_request()


if __name__ == '__main__':
    main()
