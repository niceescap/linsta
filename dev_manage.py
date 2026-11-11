"""Explicit offline test administrator creation; no server command."""
import getpass
import os
import sys

import dev_runtime

EMAIL = 'admin@digger-dev.invalid'


def create_admin(password):
    dev_runtime.check_paths()
    if len(password) < 16:
        raise ValueError('Use a fresh dev-only password of at least 16 characters')
    from db import get_db
    from werkzeug.security import generate_password_hash
    path = dev_runtime.ROOT/'digger.sqlite'
    if not path.is_file():
        raise RuntimeError('Prepare dev data first')
    old = os.umask(0o077)
    try:
        conn = get_db(path)
        try:
            with conn:
                conn.execute("INSERT INTO users(email,password_hash,display_name,role,is_verified) VALUES (?,?,?,'admin',1)",
                    (EMAIL, generate_password_hash(password), 'Local dev administrator'))
        finally:
            conn.close()
    finally:
        os.umask(old)


def main():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RuntimeError('Interactive terminal required; no password arguments, pipes or logs')
    password = getpass.getpass('Fresh DEV-ONLY password (16+ characters; never reuse beta): ')
    if password != getpass.getpass('Confirm DEV password: '):
        raise ValueError('Passwords do not match')
    create_admin(password)
    print('Created local administrator:', EMAIL)


if __name__ == '__main__':
    main()
