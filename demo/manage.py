"""Explicit offline preparation, or foreground web launch. No worker command."""
import argparse
import fcntl
import getpass
import os
import resource
import sys

from demo.runtime import DATABASE, ROOT, check_paths, install_config, require_initialized


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('init', 'create-admin', 'serve'))
    args = parser.parse_args()
    os.umask(0o077)
    install_config()
    if args.action == 'init':
        if DATABASE.exists():
            parser.error('Refusing initialization over an existing database')
        for path in (ROOT, ROOT / 'storage' / 'tracks', ROOT / 'storage' / 'meta', ROOT / 'storage' / 'hls'):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        check_paths()
        # Reserve the new database atomically; never open an existing target.
        fd = os.open(DATABASE, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        from db import init_db
        init_db(DATABASE)
        print('Isolated demo initialized:', DATABASE)
        return
    require_initialized()
    if args.action == 'create-admin':
        if not sys.stdin.isatty():
            parser.error('Use an interactive terminal; password input through pipes/logs is forbidden')
        from db import get_db
        from werkzeug.security import generate_password_hash
        email = input('Demo administrator email: ').strip().lower()
        password = getpass.getpass('New demo password (minimum 16 characters): ')
        if '@' not in email or len(password) < 16 or password != getpass.getpass('Confirm password: '):
            parser.error('Invalid email or password confirmation')
        conn = get_db(DATABASE)
        try:
            with conn:
                conn.execute("INSERT INTO users(email,password_hash,display_name,role,is_verified) VALUES (?,?,?,'admin',1)",
                             (email, generate_password_hash(password), 'Demo administrator'))
        finally:
            conn.close()
        print('Demo account created; no historical account modified.')
        return
    # Advisory lock survives exec and excludes a second demo web launcher.
    lock = os.open(ROOT / 'web.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(lock, True)
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    os.nice(10)
    os.environ.pop('GUNICORN_CMD_ARGS', None)
    print(f'Digger demo master PID: {os.getpid()} — stop with SIGTERM to this PID only', flush=True)
    # Fresh process: web.py installs the demo config in the Gunicorn worker.
    os.execv(sys.executable, [sys.executable, '-B', '-m', 'gunicorn',
        '--config', 'demo/gunicorn.conf.py',
        '--bind', '127.0.0.1:17000', '--workers', '1', '--threads', '1',
        '--worker-class', 'sync', '--timeout', '30', '--graceful-timeout', '15',
        '--max-requests', '500', '--max-requests-jitter', '50', '--keep-alive', '2',
        '--worker-tmp-dir', str(ROOT), '--error-logfile', '-',
        '--name', 'digger-demo', 'demo.web:application'])


if __name__ == '__main__':
    main()
