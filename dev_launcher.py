"""Foreground-only supervisor. Own Popen handle only; no PID files or systemd."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys

import dev_runtime

REPO = Path(__file__).resolve().parent


def command(kind, seconds=3600, mode="preview"):
    if kind == 'web':
        return [sys.executable, '-B', '-m', 'gunicorn', '--config',
                str(REPO/'dev_gunicorn.py'), 'dev_web:application']
    if kind == 'radio' and mode == 'continuous':
        return [sys.executable, '-B', '-m', 'dev_radio', '--mode', 'continuous']
    if mode != 'preview':
        raise ValueError('Unknown mode')
    if kind != 'radio' or type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError('Radio duration must be 1..3600 seconds')
    return [sys.executable, '-B', '-m', 'dev_radio', '--seconds', str(seconds)]


def run(kind, seconds=3600, mode="preview"):
    args = command(kind, seconds, mode)
    dev_runtime.check_paths()
    if not (dev_runtime.ROOT/'digger.sqlite').is_file():
        raise RuntimeError('Dev data must be prepared first')
    old_mask = os.umask(0o077)
    lock = None
    child = None
    handlers = {}
    try:
        lock = os.open(dev_runtime.ROOT/f'launcher-{kind}.lock',
                       os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dev_runtime.check_paths()
        # No inherited credentials, Python injection, dotenv or Gunicorn flags.
        env = {'PATH': os.defpath, 'LC_ALL': 'C.UTF-8',
               'PYTHONDONTWRITEBYTECODE': '1'}
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.signal(sig, interrupted)
        child = subprocess.Popen(args, cwd=REPO, env=env, close_fds=True,
                                 start_new_session=True)
        try:
            return child.wait()
        except KeyboardInterrupt:
            return 130
    finally:
        # A second terminal signal must not interrupt child cleanup.
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        # Signal only the direct child we created, never a caller-supplied PID.
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        # Any surviving descendants belong to the session/group created above.
        # No process group supplied by CLI, files, HTTP or production is accepted.
        if child is not None and type(child.pid) is int:
            import time
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.2)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if lock is not None:
            os.close(lock)
        os.umask(old_mask)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('web', 'radio'))
    parser.add_argument('--seconds', type=int, default=3600)
    parser.add_argument('--mode', choices=('preview', 'continuous'), default='preview')
    args = parser.parse_args()
    raise SystemExit(run(args.kind, args.seconds, args.mode))


if __name__ == '__main__':
    main()
