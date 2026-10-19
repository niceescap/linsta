"""Bounded preview engine process. No HTTP listener, secrets, web DB or migration."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import tempfile
import time
import uuid

import private_radio


def write_marker(root, data):
    fd, name = tempfile.mkstemp(prefix='.preview-run-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / 'preview-run.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(root, seconds, *, worker_factory=private_radio.load_worker):
    root = Path(root)
    if not root.is_absolute() or not 1 <= seconds <= 3600:
        raise ValueError('An absolute existing root and a duration of 1–3600 seconds are required')
    os.umask(0o077)
    private_radio.safe_root(root)
    with private_radio.catalogue_lock(root):
        private_radio.tracks(root)  # Existing approved-for-private-test files only.
        worker = worker_factory(root)
        invocation = os.environ.get('INVOCATION_ID', '')
        if not re.fullmatch('[0-9a-f]{32}', invocation):
            invocation = uuid.uuid4().hex  # Isolated tests; no public HTTP in this process.
        marker = {'invocation_id': invocation, 'started_at': time.time(), 'state': 'running'}
        write_marker(root, marker)
        previous = {}
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, worker.handle_sigterm)
            private_radio.play_loop(root, worker, seconds=seconds)
        finally:
            worker.STOP = True
            marker['state'] = 'stopped'
            write_marker(root, marker)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=3600)
    args = parser.parse_args()
    run(args.root, args.seconds)


if __name__ == '__main__':
    main()
