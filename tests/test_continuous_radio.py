"""Synthetic-only continuous engine checks; no network listeners or systemd."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import private_radio as radio
import dev_runtime
import continuous_radio_runner as runner
import test_private_radio as fixture


class ContinuousTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PrivateRadioTests.setUpClass.__func__)
    setUp = fixture.PrivateRadioTests.setUp
    imported = fixture.PrivateRadioTests.imported

    def fake_worker(self):
        worker=SimpleNamespace(STOP=False, write_state=Mock(), clear_hls_dir=Mock())
        return worker

    def test_simulated_two_hours_multiple_tracks_no_timer(self):
        self.imported()
        worker=self.fake_worker()
        clock=[0]
        played=[]
        engine=Mock()
        def play(path):
            played.append(Path(path).name)
            clock[0]+=1000
            if clock[0]>=8000: worker.STOP=True
            return True
        engine.play.side_effect=play
        with patch('private_radio.time.time', side_effect=lambda:clock[0]), patch('private_radio.threading.Timer') as timer:
            radio.play_loop(self.root, worker, seconds=None, continuous=True, engine=engine)
        timer.assert_not_called()
        self.assertEqual(len(played),8)
        self.assertEqual(set(played),{'one.mp3','two.mp3'})
        self.assertGreater(clock[0],7200)
        engine.start.assert_called_once()
        engine.stop.assert_called_once()
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('select count(*) from private_plays where ended_at is null').fetchone()[0],0)

    def test_preview_still_has_timer_and_limit(self):
        self.imported()
        worker=self.fake_worker()
        engine=Mock()
        def play(path): worker.STOP=True; return True
        engine.play.side_effect=play
        with patch('private_radio.threading.Timer') as timer:
            radio.play_loop(self.root,worker,seconds=3600,engine=engine)
            self.assertEqual(timer.call_args.args[0],3600)
            timer.return_value.start.assert_called_once()
            timer.return_value.cancel.assert_called_once()
        with self.assertRaises(ValueError):
            radio.play_loop(self.root,worker,seconds=3601,engine=Mock())

    def test_decode_failure_stops_without_retry_and_cleans_up(self):
        self.imported()
        for failure in (False,False,False):
            worker=self.fake_worker();engine=Mock();engine.play.return_value=failure
            with self.assertRaises(ValueError):
                radio.play_loop(self.root,worker,seconds=None,continuous=True,engine=engine)
            engine.play.assert_called_once()
            engine.stop.assert_called_once()

    def test_runner_marks_failure_and_does_not_restart(self):
        self.imported()
        worker=self.fake_worker()
        with patch.object(dev_runtime,'ROOT',self.root.parent), patch.object(dev_runtime,'check_paths'), patch('continuous_radio_runner.private_radio.play_loop',side_effect=RuntimeError('test')) as loop:
            with self.assertRaises(RuntimeError):runner.run(self.root,worker_factory=lambda root:worker)
        loop.assert_called_once()
        self.assertEqual(json.loads((self.root/'preview-run.json').read_text())['state'],'failed')
        self.assertTrue(worker.STOP)
        with radio.catalogue_lock(self.root): pass

    def test_manual_stop_records_marker(self):
        self.imported()
        worker=self.fake_worker()
        (self.root/'hls/seg_999.ts.tmp').write_bytes(b'unpublished')
        (self.root/'hls/stream.m3u8.tmp').write_bytes(b'unpublished')
        def play(*args,**kwargs):signal.raise_signal(signal.SIGTERM)
        with patch.object(dev_runtime,'ROOT',self.root.parent), patch.object(dev_runtime,'check_paths'), patch('continuous_radio_runner.private_radio.play_loop',side_effect=play):
            runner.run(self.root,worker_factory=lambda root:worker)
        self.assertTrue(worker.STOP)
        self.assertFalse((self.root/'hls/seg_999.ts.tmp').exists())
        self.assertFalse((self.root/'hls/stream.m3u8.tmp').exists())
        self.assertEqual(json.loads((self.root/'preview-run.json').read_text())['state'],'stopped')

    def test_real_synthetic_hls_window_progress_and_cleanup(self):
        self.imported()
        worker=radio.load_worker(self.root)
        pids=[];observed=[]
        class Engine(worker.Engine):
            def play(inner,path):
                pids.append(inner.encoder.pid)
                result=super().play(path)
                files=list((self.root/'hls').glob('seg_*.ts'))
                observed.append(len(files))
                if len(pids)>=16:worker.STOP=True
                return result
        engine=Engine(threads=1,continuous=True)
        # Hard test deadline only, not a continuous-engine expiry.
        import threading
        deadline=threading.Timer(48,lambda:setattr(worker,'STOP',True));deadline.start()
        try:
            radio.play_loop(self.root,worker,seconds=None,continuous=True,engine=engine)
        finally:
            deadline.cancel()
            engine.stop()
        self.assertEqual(len(pids),16)
        self.assertEqual(len(set(pids)),1)
        self.assertLessEqual(max(observed),8)
        text=(self.root/'hls/stream.m3u8').read_text()
        self.assertIn('#EXT-X-MEDIA-SEQUENCE:',text)
        self.assertIn('#EXT-X-PROGRAM-DATE-TIME:',text)
        self.assertLessEqual(len([x for x in text.splitlines() if x.endswith('.ts')]),6)
        self.assertIsNone(engine.encoder)
        self.assertIsNone(engine.decoder)
        self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in set(pids)))
        # Next start only deletes HLS generated files, never catalogue or audio.
        worker.clear_hls_dir()
        self.assertFalse(list((self.root/'hls').glob('seg_*.ts')))
        self.assertTrue((self.root/'one.mp3').exists())

if __name__=='__main__':unittest.main()
