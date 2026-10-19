"""Deterministic regression checks: no FFmpeg, sockets or real catalogue."""
import queue
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import private_radio as radio


class PrivateHLSContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'radio'
        radio.initialize(self.root)
        self.worker=radio.load_worker(self.root)

    def publish(self, sequence):
        p=self.root/'hls'/f'seg_{sequence}.ts'
        p.write_bytes(b'segment')
        (self.root/'hls/stream.m3u8').write_text('#EXTM3U\n'+p.name+'\n')

    def test_input_wait_over_20_seconds_keeps_encoder_fed_and_watchdog_unchanged(self):
        w=self.worker
        engine=w.Engine(continuous=True)
        engine.encoder=Mock()
        engine.encoder.poll.return_value=None
        now=[0.0]
        sent=[]
        audio=b'\x01\x00\x01\x00'*8820
        class Clock:
            stopped=False
            supplied=False
            def is_set(self): return self.stopped
            def wait(self, delay):
                now[0]+=delay
                if now[0]>=25 and not self.supplied:
                    engine.pcm_queue.put(audio)
                    self.supplied=True
                if now[0]>=27: self.stopped=True
        engine.pump_stop=Clock()
        engine.last_progress=0
        def consume(pcm):
            # A working encoder publishes only when it actually receives PCM.
            self.publish(int(now[0]//4))
            engine.check()
            sent.append((now[0],pcm))
        with patch.object(w.time,'monotonic',side_effect=lambda:now[0]), \
                patch.object(engine,'_send_pcm',side_effect=consume):
            engine._pump_pcm()
        self.assertIsNone(engine.pump_error)
        self.assertEqual(w.HEALTH_TIMEOUT,20)
        self.assertEqual(w.STARTUP_TIMEOUT,20)
        self.assertGreaterEqual(len([t for t,p in sent if not any(p)]),120)
        self.assertIn(audio,[p for t,p in sent])
        self.assertEqual(engine.pcm_queue.unfinished_tasks,0)
        self.assertEqual(engine.pcm_queue.maxsize,16)

    def test_frozen_encoder_still_fails_after_20_seconds(self):
        e=self.worker.Engine(continuous=True)
        e.encoder=Mock()
        e.encoder.poll.return_value=None
        self.publish(1)
        with patch.object(self.worker.time,'monotonic',return_value=0):e.check()
        with patch.object(self.worker.time,'monotonic',return_value=20.1),self.assertRaises(self.worker.PipelineError):e.check()

    def test_nonblocking_partial_writes_and_eagain_preserve_every_byte(self):
        w=self.worker
        e=w.Engine()
        e.encoder=Mock()
        e.encoder.stdin.fileno.return_value=12
        written=[]
        calls=[BlockingIOError(),2,3]
        def write(fd, data):
            result=calls.pop(0)
            if isinstance(result,Exception): raise result
            written.append(bytes(data[:result]))
            return result
        with patch.object(e,'check'), patch.object(w.select,'select',return_value=([],[12],[])), \
                patch.object(w.os,'write',side_effect=write):
            e.send(b'abcde')
        self.assertEqual(b''.join(written),b'abcde')

    def test_pump_failure_propagates_and_stop_joins_and_drains(self):
        w=self.worker
        e=w.Engine(continuous=True)
        e.pcm_queue.put(b'abcd')
        with patch.object(e,'_send_pcm',side_effect=w.PipelineError('encoder stalled')):
            e._pump_pcm()
        with self.assertRaisesRegex(w.PipelineError,'encoder stalled'): e.check()
        self.assertEqual(e.pcm_queue.unfinished_tasks,0)
        e.pcm_queue.put(b'queued')
        thread=e.pump=Mock()
        with patch.object(w,'stop_process') as stop:
            e.stop()
        thread.join.assert_called_once_with(timeout=2)
        self.assertEqual(stop.call_count,2)
        self.assertTrue(e.pump_stop.is_set())
        self.assertIsNone(e.pump)
        self.assertEqual(e.pcm_queue.unfinished_tasks,0)
        self.assertIsNone(e.encoder)
        self.assertIsNone(e.decoder)

    def test_decoder_starvation_is_distinct_and_bounded(self):
        w=self.worker
        e=w.Engine(continuous=True)
        decoder=Mock()
        decoder.stdout.fileno.return_value=13
        decoder.poll.return_value=None
        with patch.object(w.subprocess,'Popen',return_value=decoder),patch.object(e,'check'), \
                patch.object(w.select,'select',return_value=([],[],[])), \
                patch.object(w.time,'monotonic',side_effect=[0,61]),patch.object(w,'stop_process') as stop:
            with self.assertRaisesRegex(w.PipelineError,'decodeur sans PCM'):e.play('synthetic.mp3')
        stop.assert_called_once_with(decoder)
        self.assertIsNone(e.decoder)

    def test_silence_cannot_hide_an_inactive_source_forever(self):
        w=self.worker
        e=w.Engine(continuous=True)
        now=[0.0]
        class Clock:
            def is_set(self): return False
            def wait(self,delay): now[0]+=delay
        e.pump_stop=Clock()
        with patch.object(w.time,'monotonic',side_effect=lambda:now[0]),patch.object(e,'_send_pcm'):
            e._pump_pcm()
        self.assertIn('source PCM inactive',str(e.pump_error))
        self.assertLess(now[0],61)

    def test_audio_clock_does_not_accumulate_scheduler_delay(self):
        w=self.worker
        e=w.Engine(continuous=True)
        e.pcm_queue=Mock()
        e.pcm_queue.get_nowait.return_value=bytes(w.PCM_CHUNK)
        now=[0.0]
        class Clock:
            count=0
            def is_set(self): return self.count>=1000
            def wait(self,delay):
                self.count+=1
                now[0]+=delay+0.003  # 3 ms of scheduler delay every packet.
        e.pump_stop=Clock()
        with patch.object(w.time,'monotonic',side_effect=lambda:now[0]),patch.object(e,'_send_pcm'):
            e._pump_pcm()
        self.assertAlmostEqual(now[0],200.003,places=5)
        self.assertEqual(e.pcm_queue.task_done.call_count,1000)

    def test_bounded_queue_applies_backpressure_without_dropping_audio(self):
        import threading
        w=self.worker
        e=w.Engine(continuous=True)
        for _ in range(16): e.pcm_queue.put(b'old')
        with patch.object(e,'check'):
            sender=threading.Thread(target=e.send,args=(b'new',))
            sender.start()
            try:
                sender.join(timeout=0.05)
                self.assertTrue(sender.is_alive())
                self.assertEqual(e.pcm_queue.get_nowait(),b'old')
                e.pcm_queue.task_done()
                sender.join(timeout=1)
                self.assertFalse(sender.is_alive())
                self.assertEqual(e.pcm_queue.qsize(),16)
                self.assertEqual(list(e.pcm_queue.queue)[-1],b'new')
            finally:
                e.pump_stop.set()
                sender.join(timeout=1)

    def test_queued_tail_is_drained_before_play_returns_and_decoder_has_one_clock(self):
        w=self.worker
        e=w.Engine(continuous=True)
        decoder=Mock()
        decoder.stdout.fileno.return_value=13
        decoder.wait.return_value=0
        tail=[]
        def drain(_):
            tail.append(e.pcm_queue.get_nowait())
            e.pcm_queue.task_done()
        with patch.object(w.subprocess,'Popen',return_value=decoder) as spawn,patch.object(e,'check'), \
                patch.object(w.select,'select',return_value=([13],[],[])), \
                patch.object(w.os,'read',side_effect=[b'abcd',b'']),patch.object(w,'stop_process'), \
                patch.object(e.pump_stop,'wait',side_effect=drain):
            self.assertTrue(e.play('synthetic.mp3'))
        self.assertNotIn('-re',spawn.call_args.args[0])
        self.assertEqual(tail,[b'abcd'])
        self.assertEqual(e.pcm_queue.unfinished_tasks,0)

    def test_encoder_start_and_restart_clear_stop_event_and_use_nonblocking_pipe(self):
        w=self.worker
        for continuous in (False,True):
            e=w.Engine(continuous=continuous)
            e.pump_stop.set()
            encoder=Mock()
            encoder.stdin.fileno.return_value=14
            with patch.object(w,'start_encoder',return_value=encoder),patch.object(w.os,'set_blocking') as nonblocking, \
                    patch.object(w.threading,'Thread') as thread:
                e.start()
                self.assertFalse(e.pump_stop.is_set())
                nonblocking.assert_called_once_with(14,False)
                self.assertEqual(thread.call_count,int(continuous))

    def test_pump_is_the_only_hls_observer_and_unexpected_exit_is_not_hidden(self):
        e=self.worker.Engine(continuous=True)
        e.encoder=Mock()
        e.encoder.poll.return_value=None
        e.pump=Mock()
        e.pump.is_alive.return_value=True
        with patch.object(Path,'read_text',side_effect=AssertionError('duplicate HLS observation')):
            e.check()
        e.pump.is_alive.return_value=False
        with self.assertRaisesRegex(self.worker.PipelineError,'horloge PCM'):e.check()


if __name__=='__main__':unittest.main()
