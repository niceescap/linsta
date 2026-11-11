"""Temporary synthetic data only; Flask client, no listener, engine or systemd."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dev_runtime


class DevIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)/'dev'
        p = patch.object(dev_runtime, 'ROOT', cls.root)
        p.start()
        cls.addClassCleanup(p.stop)
        p = patch.dict(os.environ, {}, clear=True)
        p.start()
        cls.addClassCleanup(p.stop)
        with patch('subprocess.run', side_effect=AssertionError('No processes')):
            dev_runtime.prepare()
        os.environ.update(DIGGER_DEMO_SETTINGS='/must/not/read/beta.json',
                          DIGGER_GOOGLE_OAUTH_ENABLED='1',
                          GOOGLE_CLIENT_SECRET='inherited-secret')
        with patch('subprocess.run', side_effect=AssertionError('No systemd')):
            import dev_web
        cls.app = dev_web.application
        cls.app.config['TESTING'] = True

    def test_configuration_and_empty_fixtures(self):
        import config
        import sqlite3
        self.assertEqual(config.DB_PATH, str(self.root/'digger.sqlite'))
        self.assertEqual(config.FLASK_HOST, '127.0.0.1')
        self.assertEqual(config.FLASK_PORT, 17010)
        self.assertEqual(config.GOOGLE_CLIENT_SECRET, '')
        self.assertNotIn('DIGGER_DEMO_SETTINGS', os.environ)
        self.assertNotIn('GOOGLE_CLIENT_SECRET', os.environ)
        self.assertEqual(self.app.config['SESSION_COOKIE_NAME'], 'digger_dev_session')
        self.assertEqual(self.app.config['PREVIEW_RADIO_ROOT'], str(self.root/'private-radio'))
        with sqlite3.connect(self.root/'digger.sqlite') as conn:
            self.assertEqual(conn.execute('select count(*) from users').fetchone()[0], 0)
        with sqlite3.connect(self.root/'private-radio/catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('select count(*) from private_tracks').fetchone()[0], 0)
        self.assertTrue((self.root/'fixtures/synthetic-silence.wav').is_file())
        self.assertEqual(list((self.root/'private-radio/hls').iterdir()), [])

    def test_local_admin_login_requires_csrf_and_keeps_hls_private(self):
        import dev_manage
        import sqlite3
        password='unique-synthetic-admin-test-password'
        dev_manage.create_admin(password)
        try:
            client=self.app.test_client()
            base='http://127.0.0.1:17010'
            credentials={'email':dev_manage.EMAIL, 'password':password}
            self.assertEqual(client.post('/login', json=credentials, base_url=base).status_code, 400)
            token=client.get('/csrf-token', base_url=base).json['csrf_token']
            self.assertEqual(client.post('/login', json=credentials, headers={'X-CSRF-Token':token}, base_url=base).status_code, 200)
            self.assertEqual(client.get('/admin', base_url=base).status_code, 200)
            self.assertEqual(client.get('/hls/stream.m3u8', base_url=base).status_code, 404)
            self.assertEqual(client.post('/admin/preview-radio/start', base_url=base).status_code, 400)
            with sqlite3.connect(self.root/'digger.sqlite') as conn:
                conn.execute("UPDATE users SET role='contributor' WHERE email=?", (dev_manage.EMAIL,))
            self.assertEqual(client.get('/admin', base_url=base).status_code, 403)
        finally:
            with sqlite3.connect(self.root/'digger.sqlite') as conn:
                conn.execute('DELETE FROM users WHERE email=?', (dev_manage.EMAIL,))

    def test_reader_connections_do_not_control_local_engine(self):
        import json, time, sqlite3
        import private_radio, dev_manage
        root=self.root/'private-radio'
        dev_manage.create_admin('synthetic-test-private-reader-password')
        started=time.time()-1
        marker=dict(state='running',invocation_id='a'*32,started_at=started)
        (root/'preview-run.json').write_text(json.dumps(marker))
        (root/'preview-run.json').chmod(0o600)
        playlist=root/'hls/stream.m3u8'
        segment=root/'hls/seg_1.ts'
        playlist.write_text('#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg_1.ts\n')
        segment.write_bytes(b'synthetic transport fixture')
        playlist.chmod(0o600); segment.chmod(0o600)
        base='http://127.0.0.1:17010'
        controller=self.app.config['PREVIEW_RADIO_CONTROLLER']
        try:
            with private_radio.catalogue_lock(root), patch('subprocess.Popen',side_effect=AssertionError('No process control')):
                for _ in range(2):
                    client=self.app.test_client()
                    with sqlite3.connect(self.root/'digger.sqlite') as conn:
                        uid=conn.execute('select id from users where email=?',(dev_manage.EMAIL,)).fetchone()[0]
                    with client.session_transaction(base_url=base) as session: session['user_id']=uid
                    response=client.get('/preview-radio/hls/stream.m3u8',base_url=base)
                    self.assertEqual(response.status_code,200)
                    response.close()
                    self.assertEqual(controller.inspect()['state'],'active')
        finally:
            for path in (root/'preview-run.json',playlist,segment): path.unlink()
            with sqlite3.connect(self.root/'digger.sqlite') as conn:
                conn.execute('delete from users where email=?',(dev_manage.EMAIL,))

    def test_foreign_root_rejected(self):
        with patch.dict(os.environ, {'DIGGER_DATA_ROOT':'/home/jean/digger/instance/demo'}):
            with self.assertRaises(RuntimeError):
                dev_runtime.check_paths()

    def test_symlink_and_hardlink_rejected(self):
        link=self.root/'escape'
        link.symlink_to('/home/jean/digger/instance/demo')
        try:
            with self.assertRaises(RuntimeError): dev_runtime.check_paths()
        finally:
            link.unlink()
        os.link(self.root/'session.secret', link)
        try:
            with self.assertRaises(RuntimeError): dev_runtime.check_paths()
        finally:
            link.unlink()

    def test_symlink_blocked_before_database_access(self):
        link=self.root/'escape'
        link.symlink_to('/home/jean/digger/instance/demo')
        try:
            with patch('db.sqlite3.connect', side_effect=AssertionError('No DB access')):
                with self.assertRaises(RuntimeError):
                    self.app.test_client().get('/', base_url='http://127.0.0.1:17010')
        finally:
            link.unlink()

    def test_no_overwrite(self):
        before=(self.root/'session.secret').read_bytes()
        with self.assertRaises(RuntimeError): dev_runtime.prepare()
        self.assertEqual((self.root/'session.secret').read_bytes(), before)

    def test_no_process_control(self):
        from preview_radio_web import PreviewError
        with patch('subprocess.run', side_effect=AssertionError('No systemd')):
            controller=self.app.config['PREVIEW_RADIO_CONTROLLER']
            self.assertIn(controller.inspect()['state'], ('unavailable', 'inactive'))
            for action in ('start','stop','restart'):
                with self.assertRaises(PreviewError): controller.action(action)

    def test_private_routes_and_csrf(self):
        client=self.app.test_client()
        base='http://127.0.0.1:17010'
        with patch('subprocess.run', side_effect=AssertionError('No processes')):
            for path in ('/admin', '/admin/preview-radio', '/preview-radio/hls/stream.m3u8'):
                self.assertEqual(client.get(path, base_url=base).status_code, 401)
            self.assertEqual(client.get('/hls/stream.m3u8', base_url=base).status_code, 404)
            self.assertEqual(client.get('/login/google', base_url=base).status_code, 403)
            self.assertEqual(client.post('/admin/preview-radio/start', base_url=base).status_code, 400)
            self.assertEqual(client.get('/', base_url='http://example.test').status_code, 403)
            self.assertEqual(client.get('/login/local', base_url=base).status_code, 200)
            self.assertEqual(client.get('/login/local', base_url=base).headers['Cache-Control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
