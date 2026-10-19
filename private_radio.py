"""Bounded, authenticated loopback-only radio laboratory using worker.Engine.

Never imports production config, modifies the contribution workflow or downloads.
The private SQLite catalogue is independent of the application database.
"""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import secrets
import signal
import sqlite3
import stat
import sys
import threading
import time
import types
import urllib.request
import urllib.error

from audio_storage import checked_open, identity
from mutagen.mp3 import MP3

ROOT = Path(__file__).resolve().parent / 'instance/demo/private-radio'
PORT = 17001
USERNAME = 'digger-test'
SEGMENT = re.compile(r'seg_[0-9]+\.ts\Z')


def safe_root(root):
    root = Path(root).absolute()
    for path in (root, root/'hls', root/'catalogue.sqlite', root/'access.key', root/'radio.lock'):
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError('Private radio paths must not contain symlinks')
        if path.exists():
            info = path.stat()
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Private radio paths must be owned by the user and private')
            if not path.is_dir() and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('Private radio files must be regular and have one link')
    return root


def connect(root):
    safe_root(root)
    # Existing-only: a typo or missing initialization must not create a catalogue.
    conn = sqlite3.connect((root/'catalogue.sqlite').as_uri()+'?mode=rw', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


@contextmanager
def catalogue_lock(root):
    """One exclusive lock for playback, migration and catalogue writes."""
    safe_root(root)
    fd = os.open(root/'radio.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Radio privée ou modification en cours : réessayer après son arrêt.') from None
        yield
    finally:
        os.close(fd)


def initialize(root):
    os.umask(0o077)
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root/'hls').mkdir(exist_ok=True, mode=0o700)
    for path in (root/'catalogue.sqlite', root/'access.key'):
        if path.exists():
            raise ValueError('Private radio already initialized; nothing overwritten')
    fd = os.open(root/'catalogue.sqlite', os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
    os.close(fd)
    conn = connect(root)
    try:
        with conn:
            conn.executescript('''
            CREATE TABLE private_tracks (
                id INTEGER PRIMARY KEY, position INTEGER NOT NULL UNIQUE,
                filename TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL,
                sha256 TEXT NOT NULL, size INTEGER NOT NULL, duration REAL NOT NULL,
                private_rights_confirmed INTEGER NOT NULL CHECK(private_rights_confirmed=1),
                validated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE private_plays (
                id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL REFERENCES private_tracks(id),
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT);
            ''')
    finally:
        conn.close()
    fd = os.open(root/'access.key', os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(secrets.token_urlsafe(32))


def validate_file(root, name, *, digest=None, size=None):
    if (not isinstance(name,str) or not name.lower().endswith('.mp3')
            or len(name.encode('utf-8')) > 255 or '/' in name or '\\' in name
            or any(ord(c)<32 or ord(c)==127 for c in name)):
        raise ValueError('Use a local MP3 basename, never a path, URL or control character')
    stream, snapshot = checked_open(root,name,sha256=digest,size=size)
    with stream:
        if snapshot[2] > 50*1024*1024 or os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError('MP3 must have one link and be at most 50 MiB')
        info = MP3(stream).info
        if (info.layer != 3 or info.sketchy or info.channels not in (1,2)
                or not math.isfinite(info.length) or not 0 < info.length <= 1800):
            raise ValueError('Invalid or excessive MP3')
        stream.seek(0)
        sha = hashlib.sha256()
        for chunk in iter(lambda:stream.read(65536),b''):
            sha.update(chunk)
        if identity(os.fstat(stream.fileno())) != snapshot:
            raise ValueError('MP3 changed during validation')
        return sha.hexdigest(), snapshot[2], float(info.length)


def import_playlist(root, manifest, *, confirmed=False):
    safe_root(root)
    if confirmed is not True:
        raise ValueError('Explicit administrator confirmation of private-test rights required')
    entries = json.loads(Path(manifest).read_text())
    if not isinstance(entries,list) or not 1 <= len(entries) <= 30:
        raise ValueError('Expected 1–30 local tracks')
    rows = []
    for position, entry in enumerate(entries):
        if set(entry) != {'file','title','artist'}:
            raise ValueError('Each entry requires only file, title and artist')
        for field in ('title','artist'):
            value = entry[field]
            if not isinstance(value,str) or not 1 <= len(value.strip()) <= 256 or any(ord(c)<32 for c in value):
                raise ValueError('Invalid track metadata')
        digest,size,duration = validate_file(root,entry['file'])
        rows.append((position,entry['file'],entry['title'],entry['artist'],digest,size,duration))
    conn = connect(root)
    try:
        with catalogue_lock(root), conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT COUNT(*) FROM private_tracks').fetchone()[0]:
                raise ValueError('Existing private catalogue is immutable; no tracks replaced')
            conn.executemany('''INSERT INTO private_tracks(position,filename,title,artist,sha256,size,duration,
                private_rights_confirmed) VALUES (?,?,?,?,?,?,?,1)''', rows)
    finally:
        conn.close()


def tracks(root):
    conn = connect(root)
    try:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(private_tracks)')}
        selection = ' AND enabled=1 AND removed_at IS NULL' if 'enabled' in columns else ''
        result = [dict(row) for row in conn.execute(
            'SELECT * FROM private_tracks WHERE private_rights_confirmed=1'+selection+' ORDER BY position')]
    finally:
        conn.close()
    if not result:
        raise ValueError('No validated private test tracks; import authorized MP3s first')
    for track in result:
        validate_file(root,track['filename'],digest=track['sha256'],size=track['size'])
    return result


def load_worker(root):
    if 'config' in sys.modules or 'app' in sys.modules:
        raise ValueError('Private radio requires its own process, not the web application')
    config = types.ModuleType('config')
    config.HLS_PATH = str(root/'hls')
    config.STORAGE_PATH = str(root)
    spec = importlib.util.spec_from_file_location('private_radio_worker', Path(__file__).with_name('worker.py'))
    worker = importlib.util.module_from_spec(spec)
    sys.modules['config'] = config
    try:
        spec.loader.exec_module(worker)
    finally:
        del sys.modules['config']
    return worker


def handler(root, password):
    class PrivateHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log credentials or requested URLs.

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            expected = 'Basic '+base64.b64encode((USERNAME+':'+password).encode()).decode()
            supplied = self.headers.get('Authorization','')
            if not hmac.compare_digest(supplied.encode(),expected.encode()):
                self.send_response(401)
                self.send_header('WWW-Authenticate','Basic realm="Digger private radio"')
                self.end_headers()
                return
            path = self.path
            if path == '/':
                data = b'''<!doctype html><meta charset="utf-8"><title>Private radio test</title>
<h1>Private radio test</h1><audio controls src="/hls/stream.m3u8"></audio><p id="now"></p>
<script>setInterval(async()=>{const r=await fetch('/now-playing');const d=await r.json();
document.querySelector('#now').textContent=(d.title||'Waiting')+' - '+(d.artist||'');},2000);</script>'''
                mime = 'text/html; charset=utf-8'
            elif path == '/now-playing':
                try:
                    stream,_ = checked_open(root/'hls','now.json')
                    with stream:
                        source = json.load(stream)
                except (FileNotFoundError,ValueError):
                    source = {}
                data = json.dumps({key:source.get(key) for key in
                    ('track_id','title','artist','started_at','duration','message')}).encode()
                mime = 'application/json'
            elif path == '/hls/stream.m3u8' or (path.startswith('/hls/') and SEGMENT.fullmatch(path[5:])):
                try:
                    stream,_ = checked_open(root/'hls',path[5:])
                    with stream:
                        data = stream.read()
                except ValueError:
                    self.send_error(404)
                    return
                mime = 'application/vnd.apple.mpegurl' if path.endswith('.m3u8') else 'video/mp2t'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cross-Origin-Resource-Policy','same-origin')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers()
            if not head:
                self.wfile.write(data)
    return PrivateHandler


def play_loop(root, worker, *, seconds, engine=None):
    playlist = tracks(root)
    engine = engine or worker.Engine(threads=1, continuous=True)
    timer = threading.Timer(seconds, lambda:setattr(worker,'STOP',True))
    timer.start()
    conn = connect(root)
    current = None
    try:
        worker.clear_hls_dir()
        engine.start()
        while not worker.STOP:
            for track in playlist:
                if worker.STOP:
                    break
                validate_file(root,track['filename'],digest=track['sha256'],size=track['size'])
                current = conn.execute('INSERT INTO private_plays(track_id) VALUES (?)',(track['id'],)).lastrowid
                conn.commit()
                worker.write_state(dict(id=track['id'],title=track['title'],artist=track['artist'],genre_slug='private-test'),
                                   time.time(),track['duration'])
                if not engine.play(str(root/track['filename'])) and not worker.STOP:
                    raise ValueError('Private track decode failed; stopped instead of silently approving other media')
                conn.execute('UPDATE private_plays SET ended_at=CURRENT_TIMESTAMP WHERE id=?',(current,))
                conn.commit()
                current = None
    finally:
        timer.cancel()
        engine.stop()
        if current is not None:
            conn.execute('UPDATE private_plays SET ended_at=CURRENT_TIMESTAMP WHERE id=?',(current,))
            conn.commit()
        conn.close()
        worker.write_state(message='private test stopped')


def run(root, seconds):
    with catalogue_lock(root):
        tracks(root)  # Validate under the lock, before any socket or FFmpeg.
        password = (root/'access.key').read_text()
        if len(password) < 32:
            raise ValueError('Invalid private access key')
        class PrivateServer(HTTPServer):
            def get_request(self):
                client,address = super().get_request()
                client.settimeout(3)
                return client,address
        server = PrivateServer(('127.0.0.1',PORT),handler(root,password))
        server.timeout = 2
        thread = threading.Thread(target=server.serve_forever,kwargs={'poll_interval':0.2},daemon=True)
        worker = load_worker(root)
        for sig in (signal.SIGTERM,signal.SIGINT):
            signal.signal(sig,worker.handle_sigterm)
        resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
        resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
        os.nice(10)
        thread.start()
        print(f'Private test on 127.0.0.1:{PORT}; maximum {seconds}s; no public route',flush=True)
        try:
            play_loop(root,worker,seconds=seconds)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def probe(root, *, progress_timeout=12):
    safe_root(root)
    token = base64.b64encode((USERNAME+':'+(root/'access.key').read_text()).encode()).decode()
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with client.open(f'http://127.0.0.1:{PORT}/',timeout=3):
            raise ValueError('Private endpoint unexpectedly accepts anonymous requests')
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise ValueError('Unexpected private authentication response') from None
        error.close()
    def fetch(path):
        request = urllib.request.Request(f'http://127.0.0.1:{PORT}'+path, headers={'Authorization':'Basic '+token})
        with client.open(request,timeout=3) as response:
            return response.read()
    first = fetch('/hls/stream.m3u8').decode()
    deadline = time.monotonic()+progress_timeout
    while True:
        time.sleep(0.5)
        second = fetch('/hls/stream.m3u8').decode()
        if second != first:
            break
        if time.monotonic() >= deadline:
            raise ValueError('Private playlist did not advance within the observation window')
    segments = [line for line in second.splitlines() if SEGMENT.fullmatch(line)]
    if '#EXTM3U' not in second or first == second or not segments or not fetch('/hls/'+segments[-1]):
        raise ValueError('Playlist not progressing or segment unavailable')
    state = json.loads(fetch('/now-playing'))
    if not state.get('track_id'):
        raise ValueError('No current private track metadata')
    print('Private authenticated playlist advancing, segment available, current metadata present.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('init','import','run','probe'))
    parser.add_argument('--playlist',type=Path)
    parser.add_argument('--confirm-private-test-rights',action='store_true')
    parser.add_argument('--seconds',type=int,default=300)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == 'init':
        initialize(ROOT)
        print('Private catalogue initialized; no media or application records imported. Access key not displayed.')
    elif args.action == 'import':
        if args.playlist is None:
            parser.error('--playlist is required')
        import_playlist(ROOT,args.playlist,confirmed=args.confirm_private_test_rights)
        print('Validated private test playlist imported; public catalogue untouched.')
    elif args.action == 'run':
        if not 1 <= args.seconds <= 3600:
            parser.error('Choose a bounded test between 1 and 3600 seconds')
        run(ROOT,args.seconds)
    else:
        probe(ROOT)


if __name__ == '__main__':
    main()
