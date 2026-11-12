"""Full contribution entry point, temporary DB/settings only; no live credentials."""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
import re
from unittest.mock import patch

import db
import rights
import test_flask_security as security
from submissions import SubmissionService
from submission_review import SubmissionReviewService
from test_media_storage import mp3_bytes, cover_bytes
import io


class DiggerPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.startup = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.startup.cleanup)
        root = Path(cls.startup.name)
        cls.data_root = root / 'data'
        cls.data_root.mkdir()
        db.init_db(cls.data_root / 'digger.sqlite')
        settings = root / 'settings.json'
        settings.write_text(json.dumps({'secret_key': '0123456789abcdef' * 4}))
        settings.chmod(0o600)
        environ = patch.dict(os.environ, {
            'DIGGER_DATA_ROOT': str(cls.data_root), 'DIGGER_DEMO_SETTINGS': str(settings),
            'DIGGER_GOOGLE_OAUTH_ENABLED': '1', 'DIGGER_GOOGLE_CLIENT_ID': 'fake-google-client',
            'DIGGER_GOOGLE_CLIENT_SECRET': 'fake-google-secret', 'SPOTIFY_METADATA_MODE': 'disabled',
            'GOOGLE_CLIENT_ID': 'unrelated-must-not-be-used',
            'GOOGLE_CLIENT_SECRET': 'unrelated-must-not-be-used',
        })
        environ.start()
        cls.addClassCleanup(environ.stop)
        names = ('app', 'config', 'email_utils')
        previous = {name: sys.modules.pop(name, None) for name in names}
        def restore():
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        cls.addClassCleanup(restore)
        spec = importlib.util.spec_from_file_location('publication_test_entry',
            Path(__file__).resolve().parents[1] / 'digger_web.py')
        cls.entry = importlib.util.module_from_spec(spec)
        with patch('socket.socket', side_effect=AssertionError('No startup network')), \
                patch.object(db, 'init_db', side_effect=AssertionError('No automatic init')), \
                patch.object(db, 'migrate_db', side_effect=AssertionError('No automatic migration')):
            spec.loader.exec_module(cls.entry)
        cls.module = sys.modules['app']
        cls.app = cls.entry.application
        cls.app.config['TESTING'] = True

    setUp = security.FlaskSecurityTests.setUp
    get = security.FlaskSecurityTests.get
    post = security.FlaskSecurityTests.post
    token = security.FlaskSecurityTests.token
    session_user = security.FlaskSecurityTests.session_user

    def test_data_paths_preserved_and_real_template_used(self):
        import config
        self.assertEqual(config.DB_PATH, str(self.data_root / 'digger.sqlite'))
        self.assertEqual(config.STORAGE_PATH, str(self.data_root / 'storage'))
        self.assertEqual(config.GOOGLE_CLIENT_ID, 'fake-google-client')
        self.assertEqual(config.APP_BASE_URL, 'https://dig.hqwi.re')
        self.assertEqual(self.app.config['SESSION_COOKIE_NAME'], 'digger_demo_session')
        html = self.get('/').get_data(as_text=True)
        self.assertIn('digger-logo-horizontal.png', html)
        self.assertIn('href="/login/google"', html)
        self.assertIn('data-live-enabled="false"', html)
        self.assertNotIn('/demo/login', html)

    def test_google_endpoint_is_allowed_and_uses_external_callback(self):
        with patch.object(self.module.google, 'authorize_redirect', return_value='OAuth permitted') as authorize:
            response = self.app.test_client().get('/login/google',
                base_url='http://127.0.0.1:17000', headers={
                    'X-Forwarded-Host': 'dig.hqwi.re', 'X-Forwarded-Proto': 'https',
                    'X-Forwarded-Port': '443'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(authorize.call_args.args[0], 'https://dig.hqwi.re/auth/callback')

    def test_public_radio_and_cover_remain_closed(self):
        for path in ('/hls/stream.m3u8', '/media/cover/1'):
            self.assertEqual(self.get(path).status_code, 404)
        response = self.get('/now-playing')
        self.assertIsNone(response.json['track_id'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_contribution_deposit_and_private_review_keep_legal_gate(self):
        service = SubmissionService(self.conn)
        row = service.create_draft(1, 'https://youtu.be/dQw4w9WgXcQ', fetch_metadata=False)
        sid = row['id']
        service.update_editorial(1, sid, title='Test title', artist='Test artist', genre_id=1)
        service.submit_for_review(1, sid)
        SubmissionReviewService(self.conn).accept(2, sid)
        self.session_user(1)
        response = self.post(f'/submissions/{sid}/audio', data={
            'csrf_token': self.token(),
            'audio': (io.BytesIO(mp3_bytes()), 'test.mp3'),
            'cover': (io.BytesIO(cover_bytes()), 'cover.png'),
            'declared_name': 'Test contributor', 'declared_artist': 'Test artist',
            'declarant_capacity': 'rights_holder', 'music_accepted': 'on', 'visual_accepted': 'on',
            'document_version': rights.DRAFT_DOCUMENT.version,
            'document_sha256': rights.DRAFT_DOCUMENT.sha256,
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.conn.execute('SELECT status FROM submissions WHERE id=?', (sid,)).fetchone()[0],
                         'pending_audio_review')
        self.session_user(2)
        self.assertEqual(self.get(f'/admin/submissions/{sid}/audio').status_code, 200)
        media = self.get(f'/admin/submissions/{sid}/audio/media/audio')
        self.assertEqual(media.status_code, 200)
        media.close()
        response = self.post(f'/admin/submissions/{sid}/audio/approve', data={
            'csrf_token': self.token(), 'listened': 'on', 'cover_checked': 'on'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.conn.execute('SELECT status FROM tracks').fetchone()[0], 'pending')
        self.assertFalse(rights.document_is_production_ready(rights.DRAFT_DOCUMENT.version,
            rights.DRAFT_DOCUMENT.text, rights.DRAFT_DOCUMENT.sha256))

    def test_optional_oauth_ignores_generic_credentials_and_never_loads_dotenv(self):
        from demo import runtime
        for enabled, client_id, secret in (('0', 'dedicated', 'dedicated'), ('1', '', ''),
                                           ('1', 'partial', ''), ('1', '', 'partial')):
            with self.subTest(enabled=enabled, client_id=client_id), patch.dict(sys.modules):
                sys.modules.pop('config', None)
                sys.modules.pop('app', None)
                with patch.dict(os.environ, {'DIGGER_GOOGLE_OAUTH_ENABLED': enabled,
                        'DIGGER_GOOGLE_CLIENT_ID': client_id, 'DIGGER_GOOGLE_CLIENT_SECRET': secret,
                        'DIGGER_ENV_FILE': '/must/not/be/read.env'}), \
                        patch('dotenv.load_dotenv', side_effect=AssertionError('No dotenv access')):
                    config = runtime.install_config(full_application=True, data_root=self.data_root)
                self.assertEqual((config.GOOGLE_CLIENT_ID, config.GOOGLE_CLIENT_SECRET), ('', ''))

    def test_disabled_google_is_hidden_and_both_routes_unavailable_without_network(self):
        with patch.dict(self.app.config, {'GOOGLE_OAUTH_ENABLED': False}), \
                patch.object(self.module.google, 'authorize_redirect', side_effect=AssertionError('No OAuth')), \
                patch.object(self.module.google, 'authorize_access_token', side_effect=AssertionError('No OAuth')):
            html = self.get('/').get_data(as_text=True)
            self.assertNotIn('href="/login/google"', html)
            self.assertIn('href="/login/local"', html)
            for path in ('/login/google', '/auth/callback'):
                response = self.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json['code'], 'oauth_unavailable')

    def test_local_login_uses_existing_json_endpoint_csrf_hash_and_contributor_role(self):
        with patch.dict(self.app.config, {'GOOGLE_OAUTH_ENABLED': False}):
            response = self.get('/login/local')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            html = response.get_data(as_text=True)
            self.assertIn('type="password"', html)
            token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
            self.assertEqual(self.post('/login', json={'email': 'user1@example.test',
                'password': 'test-password'}).status_code, 400)
            response = self.post('/login', json={'email': 'user1@example.test',
                'password': 'test-password'}, headers={'X-CSRF-Token': token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['user']['role'], 'contributor')
            self.assertEqual(self.get('/submissions').status_code, 200)
            self.assertEqual(self.get('/admin').status_code, 403)

    def test_local_login_refuses_invalid_password_and_unverified_account(self):
        for email, password, expected in (('user1@example.test', 'wrong', 401),
                                          ('user3@example.test', 'test-password', 403)):
            response = self.post('/login', json={'email': email, 'password': password},
                                 headers={'X-CSRF-Token': self.token()})
            self.assertEqual(response.status_code, expected)

    def test_preview_registration_stays_invitation_only(self):
        response = self.post('/register', json={}, headers={'X-CSRF-Token': self.token()})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.get('/verify/fake').status_code, 403)

    def test_uninitialized_data_root_is_not_created_or_migrated(self):
        from demo import runtime
        missing = Path(self.temp.name) / 'missing'
        with self.assertRaises(sqlite3.OperationalError):
            runtime.require_initialized(data_root=missing)
        self.assertFalse(missing.exists())
