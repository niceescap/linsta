"""Private catalogue/HTTP checks and one short real synthetic FFmpeg loop, no sockets."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import private_radio as radio
import rights


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'Local FFmpeg/FFprobe required')
class PrivateRadioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        tone = Path(cls.fixtures.name)/'tone.mp3'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin',
            '-filter_threads','1','-filter_complex_threads','1',
            '-f','lavfi','-i','sine=frequency=440:duration=2.2','-threads','1',
            '-ac','2','-ar','44100','-codec:a','libmp3lame','-y',str(tone)],
            check=True,timeout=15,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        cls.audio = tone.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'private-radio'
        radio.initialize(self.root)
        for name in ('one.mp3','two.mp3'):
            (self.root/name).write_bytes(self.audio)
        self.manifest = self.root/'playlist.json'
        self.manifest.write_text(json.dumps([
            {'file':'one.mp3','title':'Synthetic one','artist':'Local test'},
            {'file':'two.mp3','title':'Synthetic two','artist':'Local test'}]))

    def imported(self):
        radio.import_playlist(self.root,self.manifest,confirmed=True)

    def test_catalogue_requires_rights_and_retains_order_and_fingerprints(self):
        with self.assertRaises(ValueError):
            radio.import_playlist(self.root,self.manifest)
        self.imported()
        tracks = radio.tracks(self.root)
        self.assertEqual([t['filename'] for t in tracks],['one.mp3','two.mp3'])
        self.assertTrue(all(t['private_rights_confirmed']==1 and len(t['sha256'])==64 for t in tracks))
        with self.assertRaises(ValueError):
            radio.import_playlist(self.root,self.manifest,confirmed=True)
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='submissions'").fetchone())
        self.assertEqual(rights.DRAFT_DOCUMENT.version,'draft-media-v1')
        self.assertFalse(rights.document_is_production_ready(rights.DRAFT_DOCUMENT.version,
            rights.DRAFT_DOCUMENT.text,rights.DRAFT_DOCUMENT.sha256))

    def test_modified_symlink_and_external_files_are_rejected(self):
        self.imported()
        (self.root/'one.mp3').write_bytes(b'changed')
        with self.assertRaises(ValueError): radio.tracks(self.root)
        (self.root/'link.mp3').symlink_to(self.root/'two.mp3')
        for name in ('../two.mp3','https://example.test/a.mp3','link.mp3'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                radio.validate_file(self.root,name)

    def test_initialization_is_private_and_never_overwrites(self):
        key = (self.root/'access.key').read_bytes()
        self.assertEqual(self.root.stat().st_mode & 0o077,0)
        self.assertEqual((self.root/'access.key').stat().st_mode & 0o077,0)
        with self.assertRaises(ValueError): radio.initialize(self.root)
        self.assertEqual((self.root/'access.key').read_bytes(),key)

    def request(self,path,*,authenticated=False):
        cls = radio.handler(self.root,'test-only-password')
        handler = cls.__new__(cls)
        handler.path = path
        handler.headers = {}
        if authenticated:
            handler.headers['Authorization'] = 'Basic '+base64.b64encode(b'digger-test:test-only-password').decode()
        handler.wfile = io.BytesIO()
        status = []
        handler.send_response = status.append
        handler.send_header = lambda *args:None
        handler.end_headers = lambda:None
        handler.send_error = status.append
        handler.do_GET()
        return status[0],handler.wfile.getvalue()

    def test_all_private_endpoints_require_auth_and_never_serve_mp3_or_database(self):
        (self.root/'hls/stream.m3u8').write_text('#EXTM3U\nseg_001.ts\n')
        (self.root/'hls/seg_001.ts').write_bytes(b'test segment')
        (self.root/'hls/now.json').write_text(json.dumps({'title':'Synthetic','email':'not-public@example.test'}))
        for path in ('/','/now-playing','/hls/stream.m3u8','/hls/seg_001.ts'):
            self.assertEqual(self.request(path)[0],401)
            self.assertEqual(self.request(path,authenticated=True)[0],200)
        for path in ('/one.mp3','/catalogue.sqlite','/access.key','/hls/now.json',
                     '/hls/../access.key','/hls/%2e%2e/access.key','/hls/seg_001.ts.tmp'):
            self.assertEqual(self.request(path,authenticated=True)[0],404)
        self.assertNotIn(b'not-public',self.request('/now-playing',authenticated=True)[1])
        (self.root/'hls/now.json').unlink()
        outside = self.root.parent/'outside.json'
        outside.write_text('{"title":"OUTSIDE_MARKER"}')
        (self.root/'hls/now.json').symlink_to(outside)
        self.assertNotIn(b'OUTSIDE_MARKER',self.request('/now-playing',authenticated=True)[1])

    def test_default_worker_commands_unchanged_and_private_threads_bounded(self):
        worker = radio.load_worker(self.root)
        with patch.object(worker.subprocess,'Popen') as spawn:
            worker.start_encoder()
            self.assertNotIn('-threads',spawn.call_args.args[0])
            self.assertNotIn('-filter_threads',spawn.call_args.args[0])
            worker.start_encoder(threads=1)
            args = spawn.call_args.args[0]
            self.assertEqual(args[args.index('-threads')+1],'1')
            self.assertEqual(args[args.index('-filter_threads')+1],'1')

    @unittest.skipUnless(os.environ.get('DIGGER_PRIVATE_HTTP_TEST')=='1','Opt-in bounded loopback integration')
    def test_bounded_private_http_runner_with_synthetic_media(self):
        self.imported()
        code = ('import sys; from pathlib import Path; import private_radio; '
                'private_radio.run(Path(sys.argv[1]), seconds=40)')
        process = subprocess.Popen([sys.executable,'-B','-c',code,str(self.root)],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
        try:
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic()+20
            while True:
                if process.poll() is not None:
                    self.fail('Private test runner exited before readiness')
                try:
                    with client.open('http://127.0.0.1:17001/',timeout=1):
                        self.fail('Anonymous private access unexpectedly allowed')
                except urllib.error.HTTPError as error:
                    self.assertEqual(error.code,401)
                    error.close()
                    break
                except (urllib.error.URLError,TimeoutError):
                    if time.monotonic()>deadline:
                        self.fail('Private HTTP readiness deadline exceeded')
                    time.sleep(0.2)
            while not (self.root/'hls/stream.m3u8').is_file():
                if time.monotonic()>deadline:
                    self.fail('Private HLS readiness deadline exceeded')
                time.sleep(0.2)
            radio.probe(self.root)
        finally:
            if process.poll() is None:
                process.terminate()  # Only the child created for this test.
            try:
                _, errors = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.communicate(timeout=3)
                self.fail('Private test runner failed to stop within deadline')
        self.assertEqual(process.returncode,0,errors.decode(errors='replace')[-1000:])

    def test_probe_requires_auth_and_live_playlist_progress(self):
        playlist1 = b'#EXTM3U\nseg_001.ts\n'
        playlist2 = b'#EXTM3U\nseg_002.ts\n'
        class Client:
            def __init__(self,progress):
                self.responses = iter((playlist1,playlist2 if progress else playlist1,
                    b'segment',b'{"track_id":1}'))
            def open(self,request,timeout):
                if isinstance(request,str):
                    raise urllib.error.HTTPError(request,401,'Unauthorized',None,None)
                if not request.full_url.startswith('http://127.0.0.1:17001/'):
                    raise AssertionError('Non-local request')
                return io.BytesIO(next(self.responses))
        for progress in (True,False):
            with patch.object(radio.urllib.request,'build_opener',return_value=Client(progress)), \
                    patch.object(radio.time,'sleep'), patch('builtins.print'):
                if progress:
                    radio.probe(self.root)
                else:
                    with self.assertRaises(ValueError): radio.probe(self.root,progress_timeout=0)

    def test_real_engine_loops_synthetic_audio_and_produces_decodable_hls(self):
        self.imported()
        worker = radio.load_worker(self.root)
        metadata = []
        original_write = worker.write_state
        def record(track=None, *args, **kwargs):
            if track:
                metadata.append(track['title'])
            return original_write(track,*args,**kwargs)
        worker.write_state = record
        class ThreeTracks(worker.Engine):
            count = 0
            def play(self,path):
                result = super().play(path)
                self.count += 1
                if self.count == 3:
                    worker.STOP = True
                return result
        engine = ThreeTracks(threads=1)
        radio.play_loop(self.root,worker,seconds=25,engine=engine)
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            played = conn.execute('SELECT track_id,ended_at FROM private_plays ORDER BY id').fetchall()
        self.assertGreaterEqual(len(played),3)
        self.assertEqual([p[0] for p in played[:3]],[1,2,1])
        self.assertEqual(metadata[:3],['Synthetic one','Synthetic two','Synthetic one'])
        self.assertTrue(all(p[1] for p in played))
        playlist = (self.root/'hls/stream.m3u8').read_text()
        segments = [line for line in playlist.splitlines() if radio.SEGMENT.fullmatch(line)]
        self.assertTrue(segments)
        data = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
            'stream=codec_name,codec_type,sample_rate,channels','-of','json',
            str(self.root/'hls'/segments[-1])],timeout=5))
        audio = [stream for stream in data['streams'] if stream['codec_type']=='audio']
        self.assertEqual((audio[0]['codec_name'],audio[0]['sample_rate'],audio[0]['channels']),('aac','44100',2))
        self.assertIsNone(engine.encoder)
        self.assertIsNone(engine.decoder)


if __name__ == '__main__':
    unittest.main()
