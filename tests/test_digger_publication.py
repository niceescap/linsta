"""Full contribution entry point, temporary DB/settings only; no live credentials."""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
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
        env_file = root / 'fake.env'
        env_file.write_text('YOUTUBE_API_KEY=publication-test-key\n')
        environ = patch.dict(os.environ, {
            'DIGGER_DATA_ROOT': str(cls.data_root), 'DIGGER_DEMO_SETTINGS': str(settings),
            'DIGGER_ENV_FILE': str(env_file), 'GOOGLE_CLIENT_ID': 'fake-google-client',
            'GOOGLE_CLIENT_SECRET': 'fake-google-secret', 'SPOTIFY_METADATA_MODE': 'disabled',
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

    def test_missing_oauth_fails_closed_without_creating_configuration(self):
        from demo import runtime
        with patch.dict(sys.modules):
            sys.modules.pop('config', None)
            sys.modules.pop('app', None)
            with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': '', 'GOOGLE_CLIENT_SECRET': ''}), \
                    self.assertRaisesRegex(RuntimeError, 'Google OAuth configuration is required'):
                runtime.install_config(full_application=True, data_root=self.data_root)

    def test_uninitialized_data_root_is_not_created_or_migrated(self):
        from demo import runtime
        missing = Path(self.temp.name) / 'missing'
        with self.assertRaises(sqlite3.OperationalError):
            runtime.require_initialized(data_root=missing)
        self.assertFalse(missing.exists())
