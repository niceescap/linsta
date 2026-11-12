"""One bounded synthetic runner process, no network, systemd or real catalogue."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

import private_radio
from preview_radio_web import health


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/ffprobe required')
class RealPreviewRunnerTests(unittest.TestCase):
    def test_synthetic_progress_second_start_refused_and_sigterm_stops_children(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'private'
            private_radio.initialize(root)
            path = root / 'tone.mp3'
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-filter_threads', '1', '-filter_complex_threads', '1', '-f', 'lavfi',
                '-i', 'sine=frequency=440:duration=2.2', '-threads', '1', '-ac', '2',
                '-ar', '44100', '-codec:a', 'libmp3lame', str(path)],
                check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            manifest = root / 'playlist.json'
            manifest.write_text(json.dumps([{'file': path.name, 'title': 'Synthetic preview', 'artist': 'Local test'}]))
            private_radio.import_playlist(root, manifest, confirmed=True)
            original = hashlib.sha256(path.read_bytes()).hexdigest()
            env = dict(os.environ, INVOCATION_ID='d'*32)
            process = subprocess.Popen([sys.executable, '-B', '-m', 'preview_radio_runner',
                '--root', str(root), '--seconds', '30'], env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, start_new_session=True)
            class Controller:
                def inspect(self): return {'state': 'active', 'invocation_id': 'd'*32}
            children = set()
            try:
                deadline = time.monotonic() + 22
                first = None
                while True:
                    self.assertIsNone(process.poll(), 'Runner exited before HLS progression')
                    # Child PID inspection only for the isolated process created by this test.
                    children_file = Path(f'/proc/{process.pid}/task/{process.pid}/children')
                    if children_file.exists():
                        children.update(int(pid) for pid in children_file.read_text().split())
                    state = health(root, Controller())
                    if state['available']:
                        if first is None:
                            first = state['playlist']
                            second = subprocess.run([sys.executable, '-B', '-m', 'preview_radio_runner',
                                '--root', str(root), '--seconds', '1'], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
                            self.assertNotEqual(second.returncode, 0)
                        elif state['playlist'] != first:
                            break
                    if time.monotonic() > deadline:
                        self.fail('Synthetic preview did not progress within bounded observation')
                    time.sleep(0.2)
                self.assertTrue(children)
                process.terminate()  # SIGTERM only our runner, never any deployed process.
                _, errors = process.communicate(timeout=12)
                self.assertEqual(process.returncode, 0, errors.decode(errors='replace')[-1000:])
                self.assertEqual(json.loads((root/'preview-run.json').read_text())['state'], 'stopped')
                self.assertFalse(health(root, Controller())['available'])
                self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in children))
                with private_radio.catalogue_lock(root): pass
                with sqlite3.connect(root/'catalogue.sqlite') as conn:
                    plays = conn.execute('SELECT ended_at FROM private_plays').fetchall()
                    self.assertTrue(plays)
                    self.assertTrue(all(row[0] is not None for row in plays))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=5)
