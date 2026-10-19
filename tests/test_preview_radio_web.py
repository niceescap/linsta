"""Preview routes with real Flask, temporary separate catalogues, fake systemd."""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import types
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3, APIC
from PIL import Image
import private_radio
import private_radio_admin
import preview_radio_runner
from preview_radio_web import ServiceController, PreviewError, UNIT
import test_digger_publication as publication_fixture
from test_media_storage import mp3_bytes, cover_bytes


class Controller:
    def __init__(self):
        self.state = 'inactive'
        self.invocation = 'a' * 32
        self.actions = []

    def inspect(self):
        return {'state': self.state, 'invocation_id': self.invocation}

    def action(self, action):
        if action == 'start' and self.state == 'active':
            raise PreviewError('Already started', 409)
        self.actions.append(action)
        self.state = 'active' if action == 'start' else 'inactive'


class PreviewRoutesTests(unittest.TestCase):
    setUpClass = classmethod(publication_fixture.DiggerPublicationTests.setUpClass.__func__)
    get = publication_fixture.DiggerPublicationTests.get
    post = publication_fixture.DiggerPublicationTests.post
    token = publication_fixture.DiggerPublicationTests.token
    session_user = publication_fixture.DiggerPublicationTests.session_user

    def setUp(self):
        publication_fixture.DiggerPublicationTests.setUp(self)
        self.root = Path(self.temp.name) / 'private-radio'
        private_radio.initialize(self.root)
        private_radio_admin.migrate(self.root)
        conn = private_radio.connect(self.root)
        with conn:
            for i in range(1, 27):
                path = self.root / f'{i}.mp3'
                path.write_bytes(mp3_bytes())
                if i == 1:
                    tags = ID3()
                    tags.add(APIC(mime='image/png', type=3, data=cover_bytes()))
                    tags.save(path)
                raw = path.read_bytes()
                conn.execute('''INSERT INTO private_tracks(id,position,filename,title,artist,sha256,size,duration,
                    private_rights_confirmed) VALUES (?,?,?,?,?,?,?,?,1)''',
                    (i, i, path.name, f'Track {i}', 'Private artist', hashlib.sha256(raw).hexdigest(), len(raw), 2.6))
        conn.close()
        self.controller = Controller()
        self.app.config.update(PREVIEW_RADIO_ROOT=str(self.root), PREVIEW_RADIO_CONTROLLER=self.controller)

    def publish(self):
        started = time.time() - 2
        (self.root / 'preview-run.json').write_text(json.dumps({
            'invocation_id': self.controller.invocation, 'started_at': started, 'state': 'running'}))
        hls = self.root / 'hls'
        (hls / 'stream.m3u8').write_text('#EXTM3U\n#EXTINF:4,\nseg_100.ts\n')
        (hls / 'seg_100.ts').write_bytes(b'synthetic HLS segment')
        (hls / 'now.json').write_text(json.dumps({'track_id': 1, 'started_at': started + 0.5}))
        self.controller.state = 'active'
        return started

    def test_anonymous_cannot_read_any_private_route_or_control_service(self):
        for path in ('/admin/preview-radio', '/preview-radio/tracks/1/audio',
                     '/preview-radio/tracks/1/cover', '/preview-radio/hls/stream.m3u8',
                     '/preview-radio/now-playing'):
            self.assertEqual(self.get(path).status_code, 401, path)
        for action in ('start', 'stop'):
            self.assertEqual(self.post('/admin/preview-radio/' + action,
                data={'csrf_token': self.token()}).status_code, 401)
        self.assertFalse(self.controller.actions)

    def test_contributor_can_listen_stream_but_cannot_admin_or_download_tracks(self):
        self.session_user(1)
        self.assertEqual(self.get('/admin/preview-radio').status_code, 403)
        self.assertEqual(self.get('/preview-radio/tracks/1/audio').status_code, 403)
        self.assertEqual(self.post('/admin/preview-radio/start',
            data={'csrf_token': self.token()}).status_code, 403)
        self.publish()
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code, 200)
        self.assertFalse(self.controller.actions)

    def test_all_26_tracks_display_without_writing_either_catalogue(self):
        self.session_user(2)
        before_web = list(self.conn.iterdump())
        conn = sqlite3.connect(self.root / 'catalogue.sqlite')
        before_private = list(conn.iterdump())
        html = self.get('/admin/preview-radio').get_data(as_text=True)
        self.assertIn('26 tracks importés', html)
        self.assertIn('PREVIEW / TEST', html)
        self.assertEqual(html.count('<audio controls'), 26)
        self.assertIn('Actif — essai privé uniquement', html)
        self.assertEqual(before_web, list(self.conn.iterdump()))
        self.assertEqual(before_private, list(conn.iterdump()))
        conn.close()

    def test_controls_require_admin_csrf_and_start_is_unique(self):
        self.session_user(2)
        for action in ('start', 'stop'):
            self.assertEqual(self.post('/admin/preview-radio/' + action).status_code, 400)
            self.assertEqual(self.post('/admin/preview-radio/' + action,
                data={'csrf_token': 'bad'}).status_code, 400)
        self.assertEqual(self.post('/admin/preview-radio/start', data={'csrf_token': self.token()}).status_code, 303)
        self.assertEqual(self.post('/admin/preview-radio/start', data={'csrf_token': self.token()}).status_code, 409)
        self.assertEqual(self.controller.actions, ['start'])
        self.assertEqual(self.post('/admin/preview-radio/stop', data={'csrf_token': self.token()}).status_code, 303)
        self.assertEqual(self.controller.actions, ['start', 'stop'])

    def test_individual_audio_byte_ranges_and_integrity(self):
        self.session_user(2)
        response = self.get('/preview-radio/tracks/1/audio', headers={'Range': 'bytes=0-9'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, (self.root / '1.mp3').read_bytes()[:10])
        response.close()
        (self.root / '1.mp3').write_bytes(b'tampered')
        self.assertEqual(self.get('/preview-radio/tracks/1/audio').status_code, 404)

    def test_embedded_cover_and_missing_cover_logo(self):
        self.session_user(1)
        response = self.get('/preview-radio/tracks/1/cover')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        with Image.open(io.BytesIO(response.data)) as image:
            self.assertEqual(image.size, (8, 6))
        response.close()
        self.assertEqual(self.get('/preview-radio/tracks/2/cover').location, '/static/digger-icon.png')

    def test_old_playlist_is_never_live_after_stop_or_restart(self):
        self.session_user(1)
        self.publish()
        self.controller.state = 'inactive'
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code, 503)
        self.controller.state = 'active'
        self.controller.invocation = 'b' * 32
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code, 503)
        result = self.get('/preview-radio/now-playing').json
        self.assertFalse(result['available'])
        self.assertIsNone(result['track_id'])

    def test_stale_missing_and_malformed_files_fail_closed(self):
        self.session_user(1)
        self.publish()
        for name in ('stream.m3u8', 'seg_100.ts'):
            os.utime(self.root / 'hls' / name, (time.time() - 60, time.time() - 60))
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code, 503)
        self.publish()
        (self.root / 'preview-run.json').write_text('[]')
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code, 503)
        (self.root / 'preview-run.json').unlink()
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code, 503)

    def test_live_playlist_segment_and_metadata_use_private_namespace(self):
        self.session_user(1)
        self.publish()
        response = self.get('/preview-radio/hls/stream.m3u8')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.get('/preview-radio/hls/seg_100.ts').data, b'synthetic HLS segment')
        result = self.get('/preview-radio/now-playing').json
        self.assertTrue(result['available'])
        self.assertEqual(result['title'], 'Track 1')
        self.assertEqual(result['cover_url'], '/preview-radio/tracks/1/cover')
        self.assertNotIn('filename', result)
        self.assertNotIn('sha256', result)
        for path in ('/hls/stream.m3u8', '/media/cover/1', '/preview-radio/hls/now.json',
                     '/preview-radio/hls/access.key', '/preview-radio/hls/seg_999.ts'):
            self.assertEqual(self.get(path).status_code, 404, path)

    def test_private_catalogue_cannot_escape_root_or_follow_audio_symlink(self):
        self.session_user(2)
        path = self.root / '1.mp3'
        path.unlink()
        path.symlink_to(self.root / '2.mp3')
        self.assertEqual(self.get('/preview-radio/tracks/1/audio').status_code, 404)
        conn = private_radio.connect(self.root)
        with conn: conn.execute("UPDATE private_tracks SET filename='../outside.mp3' WHERE id=2")
        conn.close()
        self.assertEqual(self.get('/preview-radio/tracks/2/audio').status_code, 404)

    def test_home_is_preview_logged_in_only_and_admin_has_catalogue_link(self):
        html = self.get('/').get_data(as_text=True)
        self.assertIn('PREVIEW / TEST', html)
        self.assertIn('data-live-enabled="false"', html)
        self.assertIn('Connectez-vous', html)
        self.session_user(1)
        html = self.get('/').get_data(as_text=True)
        self.assertIn('data-live-enabled="true"', html)
        self.assertIn('data-stream-url="/preview-radio/hls/stream.m3u8"', html)
        self.assertIn('data-metadata-url="/preview-radio/now-playing"', html)
        self.session_user(2)
        self.assertIn('href="/admin/preview-radio"', self.get('/admin').get_data(as_text=True))


class ServiceControllerTests(unittest.TestCase):
    def test_commands_are_fixed_unit_no_shell_and_repeated_start_refused(self):
        controller = ServiceController()
        show = subprocess.CompletedProcess([], 0, 'LoadState=loaded\nActiveState=inactive\nInvocationID=\n', '')
        done = subprocess.CompletedProcess([], 0, '', '')
        with patch('preview_radio_web.subprocess.run', side_effect=[show, done]) as run:
            controller.action('start')
        self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'start', UNIT])
        self.assertNotIn('shell', run.call_args.kwargs)
        show.stdout = 'LoadState=loaded\nActiveState=active\nInvocationID=' + 'a'*32
        with patch('preview_radio_web.subprocess.run', return_value=show) as run:
            with self.assertRaises(PreviewError): controller.action('start')
            self.assertEqual(run.call_count, 1)
        with self.assertRaises(PreviewError): controller.action('arbitrary.service')

    def test_uninstalled_or_unreachable_service_never_triggers_start(self):
        controller = ServiceController()
        with patch('preview_radio_web.subprocess.run', side_effect=OSError):
            self.assertEqual(controller.inspect()['state'], 'unavailable')
            with self.assertRaises(PreviewError): controller.action('start')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'private'
        private_radio.initialize(self.root)

    def test_existing_lock_prevents_second_engine_before_any_validation_or_write(self):
        with private_radio.catalogue_lock(self.root), patch.object(private_radio, 'tracks') as tracks:
            with self.assertRaises(ValueError): preview_radio_runner.run(self.root, 10)
            tracks.assert_not_called()
        self.assertFalse((self.root / 'preview-run.json').exists())

    def test_runner_keeps_lock_records_invocation_and_marks_stopped_on_failure(self):
        worker = types.SimpleNamespace(STOP=False, handle_sigterm=lambda *args: None)
        def loop(root, passed_worker, **kwargs):
            with self.assertRaises(ValueError), private_radio.catalogue_lock(root): pass
            self.assertEqual(json.loads((root/'preview-run.json').read_text())['invocation_id'], 'c'*32)
            raise ValueError('synthetic decoder failure')
        with patch.object(private_radio, 'tracks'), patch.object(private_radio, 'play_loop', side_effect=loop), \
                patch.dict(os.environ, {'INVOCATION_ID': 'c'*32}):
            with self.assertRaises(ValueError):
                preview_radio_runner.run(self.root, 10, worker_factory=lambda root: worker)
        self.assertEqual(json.loads((self.root/'preview-run.json').read_text())['state'], 'stopped')
        self.assertTrue(worker.STOP)
        with private_radio.catalogue_lock(self.root): pass

    def test_runner_rejects_unbounded_session_or_relative_root(self):
        for root, seconds in ((self.root, 3601), (self.root, 0), (Path('relative'), 10)):
            with self.assertRaises(ValueError): preview_radio_runner.run(root, seconds)
