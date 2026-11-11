"""Continuous private engine. No HTTP, systemd, retry loop or production root."""
import os
import re
import signal
import threading
import time
import uuid

import private_radio
from preview_radio_runner import write_marker


def run(root, *, worker_factory=private_radio.load_worker, normalize=False):
    import dev_runtime
    dev_runtime.check_paths()
    if root != dev_runtime.ROOT/'private-radio':
        raise ValueError('Continuous radio is restricted to the dev catalogue')
    old_mask = os.umask(0o077)
    parent = os.getppid()
    done = threading.Event()
    previous = {}
    monitor = None
    worker = None
    marker = dict(invocation_id=uuid.uuid4().hex, started_at=time.time(),
                  state='running', mode='continuous')
    try:
        with private_radio.catalogue_lock(root):
            private_radio.tracks(root)
            # A killed encoder may leave an unpublished temp segment/playlist.
            # Delete only its exact generated names inside the checked dev root.
            for path in (root/'hls').iterdir():
                if path.name == 'stream.m3u8.tmp' or re.fullmatch(r'seg_[0-9]+\.ts\.tmp', path.name):
                    path.unlink()
            worker = worker_factory(root)
            worker.STOP = False
            def stop(signum, frame):
                worker.STOP = True
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, stop)
            def watch_parent():
                while not done.wait(1):
                    if os.getppid() != parent:
                        worker.STOP = True
                        return
            monitor = threading.Thread(target=watch_parent, name='dev-parent-watch', daemon=True)
            monitor.start()
            write_marker(root, marker)
            print('[continuous] started; no automatic restart', flush=True)
            try:
                options = {}
                if normalize:
                    from dev_audio_gain import MeasuredGain
                    gain = MeasuredGain(root, dev_runtime.ROOT/'volet02-levels.json')
                    options['engine'] = worker.Engine(threads=1,continuous=True,gain_resolver=gain)
                private_radio.play_loop(root, worker, seconds=None, continuous=True, **options)
                if not worker.STOP:
                    raise RuntimeError('No playable active catalogue remains')
            except Exception as exc:
                marker['state'] = 'failed'
                # Fail on the first fatal error, stricter than any retry budget.
                print('[continuous] stopped after fatal error: '+type(exc).__name__, flush=True)
                raise
            finally:
                worker.STOP = True
                if marker['state'] != 'failed':
                    marker['state'] = 'stopped'
                write_marker(root, marker)
                print('[continuous] '+marker['state'], flush=True)
    finally:
        done.set()
        if monitor is not None:
            monitor.join(timeout=2)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        os.umask(old_mask)
