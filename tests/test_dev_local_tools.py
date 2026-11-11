"""Offline local tools tests. All process creation is mocked."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import dev_launcher
import dev_manage
import dev_radio
import dev_runtime


class LocalToolsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'dev'
        p=patch.object(dev_runtime, 'ROOT', self.root)
        p.start()
        self.addCleanup(p.stop)
        p=patch.dict(os.environ, {}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        with patch('subprocess.Popen', side_effect=AssertionError('No processes')):
            dev_runtime.prepare()

    def test_admin_hash_and_no_overwrite(self):
        from werkzeug.security import check_password_hash
        password='synthetic-only-test-password-unique'
        dev_manage.create_admin(password)
        with sqlite3.connect(self.root/'digger.sqlite') as conn:
            email, hashed, role, verified=conn.execute('select email,password_hash,role,is_verified from users').fetchone()
        self.assertEqual(email, 'admin@digger-dev.invalid')
        self.assertNotEqual(hashed, password)
        self.assertTrue(check_password_hash(hashed, password))
        self.assertEqual((role, verified), ('admin', 1))
        with self.assertRaises(sqlite3.IntegrityError):
            dev_manage.create_admin('another-synthetic-password-only')
        with sqlite3.connect(self.root/'digger.sqlite') as conn:
            self.assertEqual(conn.execute('select password_hash from users').fetchone()[0], hashed)

    def test_admin_requires_terminal_and_strong_password(self):
        with self.assertRaises(ValueError): dev_manage.create_admin('short')
        with patch('sys.stdin.isatty', return_value=False), patch('getpass.getpass') as ask:
            with self.assertRaises(RuntimeError): dev_manage.main()
            ask.assert_not_called()

    def test_symlink_blocks_admin_and_launcher(self):
        link=self.root/'private-radio/hls/escape'
        link.symlink_to('/home/jean/digger/instance/demo')
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(RuntimeError): dev_launcher.run('web')
            with self.assertRaises(RuntimeError): dev_manage.create_admin('synthetic-long-password-only')
            spawn.assert_not_called()

    def test_commands_fixed_and_bounded(self):
        web=dev_launcher.command('web')
        self.assertEqual(web[-1], 'dev_web:application')
        self.assertIn(str(dev_launcher.REPO/'dev_gunicorn.py'), web)
        self.assertEqual(dev_launcher.command('radio', 60)[-3:], ['dev_radio','--seconds','60'])
        for invalid in (0, 3601, True):
            with self.assertRaises(ValueError): dev_launcher.command('radio', invalid)
            with self.assertRaises(ValueError): dev_radio.run(invalid)
        with self.assertRaises(ValueError): dev_launcher.command('systemctl')
        with patch('preview_radio_runner.run') as runner:
            dev_radio.run(60)
            runner.assert_called_once_with(self.root/'private-radio', 60)

    def test_supervisor_clean_environment_and_own_child_only(self):
        child=Mock()
        child.wait.side_effect=[KeyboardInterrupt(), 0]
        child.poll.return_value=None
        with patch.dict(os.environ, {'GUNICORN_CMD_ARGS':'--bind=0.0.0.0:17000',
                                     'PYTHONPATH':'/production', 'DIGGER_DATA_ROOT':'/production'}):
            with self.assertRaises(RuntimeError): dev_launcher.run('web')
        with patch.dict(os.environ, {'GUNICORN_CMD_ARGS':'--bind=0.0.0.0:17000',
                                     'PYTHONPATH':'/production'}), patch('subprocess.Popen', return_value=child) as spawn:
            self.assertEqual(dev_launcher.run('web'), 130)
        _, kwargs=spawn.call_args
        self.assertEqual(kwargs['cwd'], dev_launcher.REPO)
        self.assertTrue(kwargs['start_new_session'])
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        self.assertNotIn('GUNICORN_CMD_ARGS', kwargs['env'])
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()

    def test_continuous_command_and_cleanup_of_owned_group(self):
        args=dev_launcher.command('radio', mode='continuous')
        self.assertEqual(args[-2:], ['--mode', 'continuous'])
        child=Mock(pid=987654321)
        child.wait.return_value=0
        child.poll.return_value=0
        with patch('subprocess.Popen',return_value=child), patch('os.killpg',side_effect=ProcessLookupError) as kill:
            self.assertEqual(dev_launcher.run('radio',mode='continuous'),0)
        import signal
        kill.assert_called_once_with(child.pid,signal.SIGTERM)

    def test_duplicate_launcher_refused(self):
        import fcntl
        lock=os.open(self.root/'launcher-web.lock', os.O_CREAT|os.O_RDWR, 0o600)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(BlockingIOError): dev_launcher.run('web')
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
