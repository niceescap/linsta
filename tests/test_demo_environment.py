"""Small isolated checks: temporary SQLite only, no socket or server started."""
import json
import contextlib
import hashlib
import io
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from demo import runtime


class DemoEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The demo installs dedicated-process config/app modules. Restore them
        # after this fixture so subsequent private-worker tests stay isolated.
        names = ('config', 'app', 'email_utils', 'demo.web')
        previous = {name: sys.modules.get(name) for name in names}
        def restore_modules():
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
            import demo
            if previous['demo.web'] is None:
                demo.__dict__.pop('web', None)
        cls.addClassCleanup(restore_modules)
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name) / 'data'
        root.mkdir()
        settings = Path(cls.temp.name) / 'settings.json'
        settings.write_text(json.dumps({'secret_key': '0123456789abcdef' * 4}))
        settings.chmod(0o600)
        for target, value in (('ROOT', root), ('DATABASE', root / 'digger.sqlite')):
            p = patch.object(runtime, target, value)
            p.start()
            cls.addClassCleanup(p.stop)
        p = patch.dict(os.environ, {'DIGGER_DEMO_SETTINGS': str(settings),
            'YOUTUBE_API_KEY': 'historical-test-key', 'SPOTIFY_CLIENT_SECRET': 'test-only'})
        p.start()
        cls.addClassCleanup(p.stop)
        # Import dependencies without production config, then initialize ONLY our temporary DB.
        import db
        db.init_db(runtime.DATABASE)
        with patch('socket.socket', side_effect=AssertionError('No network')):
            from demo.web import application
        cls.app = application
        cls.app.config['TESTING'] = True

    def test_config_is_separate_and_inherited_credentials_removed(self):
        import config
        self.assertEqual(config.DB_PATH, str(runtime.DATABASE))
        self.assertEqual((config.FLASK_HOST, config.FLASK_PORT, config.FLASK_DEBUG),
                         ('127.0.0.1', 17000, False))
        self.assertNotIn('YOUTUBE_API_KEY', os.environ)
        self.assertNotIn('SPOTIFY_CLIENT_SECRET', os.environ)
        self.assertTrue(self.app.config['SESSION_COOKIE_SECURE'])

    def test_pages_and_admin_authorization_without_listening(self):
        client = self.app.test_client()
        for path in ('/', '/demo/login', '/now-playing'):
            self.assertEqual(client.get(path, base_url='https://dig.hqwi.re').status_code, 200)
        self.assertEqual(client.get('/admin', base_url='https://dig.hqwi.re').status_code, 401)
        response = client.get('/demo/login', base_url='https://dig.hqwi.re')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_password_login_and_demo_admin(self):
        import db
        from werkzeug.security import generate_password_hash
        conn = db.get_db(runtime.DATABASE)
        try:
            with conn:
                conn.execute("INSERT INTO users(email,password_hash,role,is_verified) VALUES (?,?,'admin',1)",
                    ('admin@example.test', generate_password_hash('temporary-test-password', method='pbkdf2:sha256:1000')))
        finally:
            conn.close()
        client = self.app.test_client()
        base = 'https://dig.hqwi.re'
        self.assertEqual(client.post('/login', base_url=base, json={}).status_code, 400)
        token = client.get('/csrf-token', base_url=base).json['csrf_token']
        response = client.post('/login', base_url=base, json={'email':'admin@example.test',
            'password':'temporary-test-password'}, headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/admin', base_url=base).status_code, 200)

    def test_external_registration_and_oauth_closed(self):
        client = self.app.test_client()
        base = 'https://dig.hqwi.re'
        for path in ('/login/google', '/auth/callback', '/verify/test'):
            self.assertEqual(client.get(path, base_url=base).status_code, 403)
        token = client.get('/csrf-token', base_url=base).json['csrf_token']
        self.assertEqual(client.post('/register', base_url=base, json={},
            headers={'X-CSRF-Token':token}).status_code, 403)

    def test_legal_gate_and_hls_stay_closed(self):
        import rights
        self.assertFalse(rights.document_is_production_ready(rights.DRAFT_DOCUMENT.version,
            rights.DRAFT_DOCUMENT.text, rights.DRAFT_DOCUMENT.sha256))
        self.assertEqual(self.app.test_client().get('/hls/stream.m3u8').status_code, 404)

    def test_symlink_data_root_is_rejected(self):
        link = Path(self.temp.name) / 'linked'
        link.symlink_to(runtime.ROOT, target_is_directory=True)
        with patch.object(runtime, 'ROOT', link), self.assertRaises(RuntimeError):
            runtime.check_paths()

    def test_initialization_refuses_existing_database_without_writing(self):
        from demo import manage
        before = hashlib.sha256(runtime.DATABASE.read_bytes()).digest()
        with patch.object(sys, 'argv', ['demo.manage', 'init']), \
                patch.object(manage, 'install_config'), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            manage.main()
        self.assertEqual(hashlib.sha256(runtime.DATABASE.read_bytes()).digest(), before)

    def test_admin_creation_requires_interactive_terminal(self):
        from demo import manage
        with patch.object(sys, 'argv', ['demo.manage', 'create-admin']), \
                patch.object(manage, 'install_config'), \
                patch.object(sys.stdin, 'isatty', return_value=False), \
                patch.object(manage.getpass, 'getpass', side_effect=AssertionError('No password prompt')), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            manage.main()

    def test_anonymous_access_for_every_registered_route(self):
        client = self.app.test_client()
        base = 'https://dig.hqwi.re'
        token = client.get('/csrf-token', base_url=base).json['csrf_token']
        public = {'index':200, 'demo_login':200, 'now_playing':200, 'csrf_token_endpoint':200,
                  'static':200, 'login':401, 'logout':200, 'logout_form':302,
                  'register':403, 'login_google':403, 'auth_google_callback':403,
                  'verify':403, 'serve_cover':404}
        for rule in self.app.url_map.iter_rules():
            path = str(rule).replace('<int:track_id>', '1').replace('<int:submission_id>', '1')
            path = path.replace('<kind>', 'audio').replace('<token>', 'test').replace('<path:filename>', 'radio-logo.png')
            expected = public.get(rule.endpoint)
            if expected is None:
                self.assertTrue(rule.endpoint.startswith(('contributions.', 'submission_review.', 'submission_audio.'))
                    or rule.endpoint in {'admin_index','approve_track','reject_track','serve_track',
                        'refetch_cover','reintegrate_track','delete_track','me','upload_track'}, rule.endpoint)
                expected = 401
            for method in sorted(rule.methods - {'HEAD','OPTIONS'}):
                with self.subTest(endpoint=rule.endpoint, method=method):
                    if method == 'POST':
                        token = client.get('/csrf-token', base_url=base).json['csrf_token']
                    result = client.open(path, method=method, base_url=base,
                        headers={'X-CSRF-Token':token}, json={} if method=='POST' else None)
                    self.assertEqual(result.status_code, expected)
                    result.close()

    def test_contributor_cannot_use_any_admin_route(self):
        import db
        conn = db.get_db(runtime.DATABASE)
        try:
            with conn:
                uid = conn.execute("INSERT INTO users(email,role,is_verified) VALUES ('role-check@example.test','contributor',1)").lastrowid
        finally:
            conn.close()
        client = self.app.test_client()
        base = 'https://dig.hqwi.re'
        with client.session_transaction(base_url=base) as session:
            session['user_id'] = uid
        token = client.get('/csrf-token', base_url=base).json['csrf_token']
        for rule in self.app.url_map.iter_rules():
            if not (str(rule).startswith('/admin') or rule.endpoint=='serve_track'):
                continue
            path = str(rule).replace('<int:track_id>','1').replace('<int:submission_id>','1').replace('<kind>','audio')
            for method in sorted(rule.methods-{'HEAD','OPTIONS'}):
                with self.subTest(endpoint=rule.endpoint, method=method):
                    self.assertEqual(client.open(path, method=method, base_url=base,
                        headers={'X-CSRF-Token':token}, json={} if method=='POST' else None).status_code, 403)

    def test_public_pages_do_not_disclose_accounts_or_unfiltered_radio_state(self):
        directory = runtime.ROOT/'storage/hls'
        directory.mkdir(parents=True, exist_ok=True)
        state = directory/'now.json'
        state.write_text(json.dumps({'email':'private-state@example.test', 'document_text':'PRIVATE_PROOF',
                                     'path':'/home/private', 'track_id':1}))
        self.addCleanup(state.unlink)
        client = self.app.test_client()
        for path in ('/', '/demo/login', '/now-playing', '/csrf-token'):
            result = client.get(path, base_url='https://dig.hqwi.re')
            self.assertEqual(result.status_code, 200)
            text = result.get_data(as_text=True)
            for marker in ('admin@example.test','role-check@example.test','private-state@example.test',
                           'PRIVATE_PROOF','/home/private','0123456789abcdef'*4):
                self.assertNotIn(marker, text)
            self.assertEqual(result.headers['Cache-Control'], 'no-store')
        home = client.get('/').get_data(as_text=True)
        self.assertNotIn('/login/google', home)
        self.assertNotIn('/admin', home)
        self.assertNotIn('cdn.jsdelivr', home)

    def test_demo_blocks_legacy_covers_and_raw_files(self):
        import db
        directory = runtime.ROOT/'storage/meta'
        directory.mkdir(parents=True, exist_ok=True)
        cover = directory/'not-authorized.jpg'
        cover.write_bytes(b'UNAUTHORIZED_COVER_MARKER')
        self.addCleanup(cover.unlink)
        conn = db.get_db(runtime.DATABASE)
        try:
            with conn:
                uid = conn.execute("INSERT INTO users(email,is_verified) VALUES ('cover-owner@example.test',1)").lastrowid
                tid = conn.execute("INSERT INTO tracks(uploader_id,file_path,title,genre_id,meta_path,status) VALUES (?,'tracks/private.mp3','Private title',1,?,'pending')",
                                   (uid,cover.name)).lastrowid
        finally:
            conn.close()
        client = self.app.test_client()
        for path in (f'/media/cover/{tid}', '/instance/demo/digger.sqlite', '/storage/meta/not-authorized.jpg',
                     '/storage/tracks/private.mp3', '/.env', '/rights.py', '/migrations/003_submission_media_authorizations.sql',
                     '/static/../instance/demo/digger.sqlite', '/hls/stream.m3u8'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)


if __name__ == '__main__':
    unittest.main()
