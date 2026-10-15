"""Real Flask/Authlib integration with isolated config, SQLite and storage.

Run with the project's declared dependencies installed. OAuth discovery/token
responses, SMTP and sockets are mocked; Authlib state validation remains real.
"""
import importlib.util
import io
import re
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from werkzeug.security import generate_password_hash
from itsdangerous import URLSafeTimedSerializer
from flask import Flask
from werkzeug.datastructures import MultiDict

from web_security import init_web_security

import db


ROOT = Path(__file__).resolve().parents[1]


class FlaskSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.import_temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.import_temp.cleanup)
        # Never import production config or load .env in the test process.
        config = types.ModuleType('config')
        values = dict(SECRET_KEY='fake-flask-test-signing-key', FLASK_HOST='localhost', FLASK_PORT=0,
            FLASK_DEBUG=False, GOOGLE_CLIENT_ID='fake-google-client', GOOGLE_CLIENT_SECRET='fake-google-secret',
            SESSION_LIFETIME_DAYS=30, SMTP_HOST='invalid.test', SMTP_PORT=0, SMTP_USER='', SMTP_PASSWORD='',
            SMTP_FROM='test@example.test', APP_BASE_URL='https://localhost', VERIFICATION_TOKEN_MAX_AGE=86400,
            DB_PATH=str(Path(cls.import_temp.name)/'never-open.sqlite'),
            STORAGE_PATH=cls.import_temp.name, TRACKS_PATH=cls.import_temp.name,
            META_PATH=cls.import_temp.name, HLS_PATH=cls.import_temp.name)
        for key, value in values.items():
            setattr(config,key,value)
        previous_email_module = sys.modules.get('email_utils')
        def restore_email():
            if previous_email_module is None:
                sys.modules.pop('email_utils',None)
            else:
                sys.modules['email_utils']=previous_email_module
        cls.addClassCleanup(restore_email)
        sys.modules.pop('email_utils',None)
        spec = importlib.util.spec_from_file_location('digger_security_test_app',ROOT/'app.py')
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'config':config}), \
                patch.object(db,'init_db',side_effect=AssertionError('No automatic initialization')), \
                patch.object(db,'migrate_db',side_effect=AssertionError('No automatic migration')), \
                patch('socket.socket',side_effect=AssertionError('No import-time network')):
            spec.loader.exec_module(cls.module)
        cls.app = cls.module.app
        # Dynamic loading under a test name otherwise changes Flask's root path.
        cls.app.template_folder = str(ROOT/'templates')
        cls.app.static_folder = str(ROOT/'static')
        cls.app.config.update(TESTING=True,SESSION_COOKIE_SECURE=True,CSRF_MAX_AGE=3600)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'test.sqlite'
        db.init_db(self.path)
        self.conn = db.get_db(self.path)
        self.addCleanup(self.conn.close)
        password = generate_password_hash('test-password',method='pbkdf2:sha256:1000')
        for uid,role,verified in ((1,'contributor',1),(2,'admin',1),(3,'contributor',0)):
            self.conn.execute("""INSERT INTO users(id,email,password_hash,display_name,role,is_verified)
                VALUES (?,?,?,?,?,?)""",(uid,f'user{uid}@example.test',password,f'User {uid}',role,verified))
        self.conn.commit()
        factory = patch.object(self.module,'get_db',side_effect=lambda:db.get_db(self.path))
        factory.start()
        self.addCleanup(factory.stop)
        for attribute in ('STORAGE_PATH','TRACKS_PATH','HLS_PATH'):
            mock = patch.object(self.module,attribute,self.temp.name)
            mock.start()
            self.addCleanup(mock.stop)
        socket = patch('socket.socket',side_effect=AssertionError('External network forbidden'))
        socket.start()
        self.addCleanup(socket.stop)
        smtp = patch.object(self.module,'send_verification_email')
        self.mail = smtp.start()
        self.addCleanup(smtp.stop)
        self.client = self.app.test_client()
        self.base = 'https://localhost'

    def get(self,path,**kwargs):
        return self.client.get(path,base_url=self.base,**kwargs)

    def token(self):
        result = self.get('/csrf-token')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers['Cache-Control'],'no-store')
        return result.json['csrf_token']

    def post(self,path,**kwargs):
        return self.client.post(path,base_url=self.base,**kwargs)

    def session_user(self,uid,role='admin'):
        with self.client.session_transaction(base_url=self.base) as session:
            session['user_id']=uid
            session['role']=role
            session['display_name']='Stale display'

    def track(self,status='pending',source_url=None):
        tid = self.conn.execute("""INSERT INTO tracks(uploader_id,file_path,title,genre_id,status,source_url)
            VALUES (1,'tracks/test.mp3','Legacy',1,?,?)""",(status,source_url)).lastrowid
        self.conn.commit()
        return tid

    def test_anonymous_access_and_public_routes(self):
        for route in ('/me','/admin','/media/999'):
            self.assertEqual(self.get(route).status_code,401)
        self.assertEqual(self.get('/').status_code,200)
        self.assertEqual(self.get('/now-playing').status_code,200)
        self.assertEqual(self.get('/media/cover/999').status_code,302)
        self.assertEqual(self.post('/tracks',headers={'X-CSRF-Token':self.token()}).status_code,401)

    def test_verified_account_loaded_from_database(self):
        self.session_user(1)
        result = self.get('/me')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['role'],'contributor')
        self.assertEqual(result.json['display_name'],'User 1')
        self.assertEqual(self.get('/admin').status_code,403)

    def test_unverified_account_session_invalidated(self):
        self.session_user(3)
        self.assertEqual(self.get('/me').status_code,403)
        with self.client.session_transaction(base_url=self.base) as session:
            self.assertNotIn('user_id',session)
        self.assertEqual(self.get('/me').status_code,401)

    def test_deleted_account_session_invalidated(self):
        self.session_user(1)
        self.conn.execute('DELETE FROM users WHERE id=1')
        self.conn.commit()
        self.assertEqual(self.get('/me').status_code,401)

    def test_malformed_session_identity_rejected(self):
        for uid in (True,'1',-1,2**63):
            self.session_user(uid)
            self.assertEqual(self.get('/me').status_code,401)

    def test_demoted_admin_denied_reads_and_mutations(self):
        self.session_user(2)
        token = self.token()
        tid = self.track()
        self.conn.execute("UPDATE users SET role='contributor' WHERE id=2")
        self.conn.commit()
        self.assertEqual(self.get('/admin').status_code,403)
        self.assertEqual(self.get(f'/media/{tid}').status_code,403)
        for action in ('approve','reject','reintegrate','delete','refetch-cover'):
            result = self.post(f'/admin/tracks/{tid}/{action}',data={'csrf_token':token})
            self.assertEqual(result.status_code,403,action)
        self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(tid,)).fetchone()[0],'pending')

    def test_current_admin_role_overrides_stale_contributor_session(self):
        self.session_user(2,role='contributor')
        self.assertEqual(self.get('/admin').status_code,200)

    def test_csrf_missing_invalid_and_cross_session(self):
        tid = self.track()
        self.session_user(2)
        for token in (None,'bad'):
            result = self.post(f'/admin/tracks/{tid}/approve',data={} if token is None else {'csrf_token':token})
            self.assertEqual(result.status_code,400)
            self.assertEqual(result.json['code'],'csrf_invalid')
        valid = self.token()
        other = self.app.test_client()
        result = other.post('/login',base_url=self.base,json={'email':'user1@example.test','password':'test-password'},
                            headers={'X-CSRF-Token':valid})
        self.assertEqual(result.status_code,400)
        self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(tid,)).fetchone()[0],'pending')

    def test_expired_csrf_and_reissue(self):
        self.session_user(2)
        self.token()
        with self.client.session_transaction(base_url=self.base) as session:
            nonce = session['_csrf_nonce']
        # Backdate only the CSRF signature, not Flask's session-cookie parsing.
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp',return_value=int(time.time())-3601):
            expired = URLSafeTimedSerializer(self.app.secret_key,salt='digger-csrf-v1').dumps(nonce)
        _, issued = URLSafeTimedSerializer(self.app.secret_key,salt='digger-csrf-v1').loads(expired,return_timestamp=True)
        self.assertGreater(time.time()-issued.timestamp(),3600)
        self.assertEqual(self.post('/logout',headers={'X-CSRF-Token':expired}).status_code,400)
        self.assertEqual(self.post('/logout',headers={'X-CSRF-Token':self.token()}).status_code,200)

    def test_login_csrf_required_and_rotated_on_success(self):
        credentials = {'email':'user1@example.test','password':'test-password'}
        self.assertEqual(self.post('/login',json=credentials).status_code,400)
        old = self.token()
        result = self.post('/login',json=credentials,headers={'X-CSRF-Token':old})
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.get('/me').json['user_id'],1)
        new = self.token()
        self.assertNotEqual(old,new)
        self.assertEqual(self.post('/logout',headers={'X-CSRF-Token':old}).status_code,400)

    def test_invalid_password_and_unverified_login(self):
        for email,password,status in (('user1@example.test','wrong',401),('user3@example.test','test-password',403)):
            result = self.post('/login',json={'email':email,'password':password},headers={'X-CSRF-Token':self.token()})
            self.assertEqual(result.status_code,status)
        self.assertEqual(self.get('/me').status_code,401)

    def test_logout_json_and_form_rotate_tokens(self):
        for path in ('/logout','/logout-form'):
            self.session_user(1)
            old = self.token()
            result = self.post(path,data={'csrf_token':old})
            self.assertEqual(result.status_code,200 if path=='/logout' else 302)
            self.assertEqual(self.get('/me').status_code,401)
            self.assertNotEqual(self.token(),old)

    def test_registration_json_uses_header_and_mocked_email(self):
        data = {'email':'new@example.test','password':'test-password','display_name':'New'}
        self.assertEqual(self.post('/register',json=data).status_code,400)
        self.assertEqual(self.post('/register',json=data,headers={'X-CSRF-Token':self.token()}).status_code,201)
        self.mail.assert_called_once()

    def test_email_verification_signed_link_remains_supported(self):
        token = self.module.generate_verification_token('user3@example.test')
        self.assertEqual(self.get('/verify/'+token).status_code,200)
        self.assertEqual(self.conn.execute('SELECT is_verified FROM users WHERE id=3').fetchone()[0],1)
        self.assertEqual(self.get('/verify/bad-token').status_code,400)

    def test_all_existing_post_forms_have_csrf_fields(self):
        self.session_user(2)
        self.track('pending')
        self.track('approved','https://youtu.be/dQw4w9WgXcQ')
        self.track('rejected')
        for route in ('/','/admin'):
            result = self.get(route)
            self.assertEqual(result.status_code,200)
            forms = re.findall(r'<form\b.*?</form>',result.get_data(as_text=True),flags=re.S)
            self.assertTrue(forms)
            for form in forms:
                self.assertIn('name="csrf_token"',form)
            self.assertEqual(result.headers['Cache-Control'],'no-store')

    def test_legacy_moderation_forms_remain_functional(self):
        self.session_user(2)
        tid = self.track()
        for action,expected in (('approve','approved'),('reject','rejected'),('reintegrate','approved')):
            result = self.post(f'/admin/tracks/{tid}/{action}',data={'csrf_token':self.token(),'reason':'Reason'})
            self.assertEqual(result.status_code,302)
            self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(tid,)).fetchone()[0],expected)
        # No source URL: existing refresh validation remains available.
        self.assertEqual(self.post(f'/admin/tracks/{tid}/refetch-cover',data={'csrf_token':self.token()}).status_code,302)
        self.assertEqual(self.post(f'/admin/tracks/{tid}/delete',data={'csrf_token':self.token()}).status_code,302)
        self.assertIsNone(self.conn.execute('SELECT id FROM tracks WHERE id=?',(tid,)).fetchone())

    def test_tracks_endpoint_stays_open_with_csrf(self):
        self.session_user(1)
        data = {'csrf_token':self.token(),'file':(io.BytesIO(b'fake-test-audio'), 'test.mp3'),
                'title':'Legacy upload','genre_id':'1'}
        with patch.object(self.module,'MutagenFile',return_value=None):
            result = self.post('/tracks',data=data,content_type='multipart/form-data')
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.conn.execute('SELECT status FROM tracks').fetchone()[0],'pending')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)

    def test_current_account_connection_closed(self):
        self.session_user(1)
        opened = []
        def factory():
            conn = db.get_db(self.path)
            opened.append(conn)
            return conn
        with patch.object(self.module,'get_db',side_effect=factory):
            self.assertEqual(self.get('/me').status_code,200)
        with self.assertRaises(sqlite3.ProgrammingError):
            opened[0].execute('SELECT 1')

    def test_no_automatic_migration_during_requests(self):
        with patch.object(db,'migrate_db',side_effect=AssertionError('Forbidden migration')), \
                patch.object(db,'init_db',side_effect=AssertionError('Forbidden init')):
            self.assertEqual(self.get('/').status_code,200)
            self.session_user(2)
            self.assertEqual(self.get('/admin').status_code,200)
            self.assertEqual(self.get('/me').status_code,200)

    def oauth_start(self):
        metadata = {'authorization_endpoint':'https://accounts.google.com/o/oauth2/v2/auth',
                    'token_endpoint':'https://oauth2.googleapis.com/token'}
        with patch.object(self.module.google,'load_server_metadata',return_value=metadata):
            result = self.get('/login/google')
        self.assertEqual(result.status_code,302)
        query = parse_qs(urlsplit(result.location).query)
        return query['state'][0]

    def test_oauth_state_validated_before_token_exchange(self):
        self.oauth_start()
        with patch.object(self.module.google,'fetch_access_token') as exchange:
            result = self.get('/auth/callback?code=fake-code&state=wrong')
        self.assertEqual(result.status_code,400)
        exchange.assert_not_called()
        self.assertEqual(self.get('/me').status_code,401)

    def test_oauth_missing_state_rejected(self):
        with patch.object(self.module.google,'fetch_access_token') as exchange:
            self.assertEqual(self.get('/auth/callback?code=fake-code').status_code,400)
        exchange.assert_not_called()

    def test_oauth_callback_preserves_state_nonce_and_rotates_csrf(self):
        old = self.token()
        state = self.oauth_start()
        info = {'email':'oauth@example.test','sub':'fake-sub','name':'OAuth User','email_verified':True}
        with patch.object(self.module.google,'fetch_access_token',return_value={'id_token':'fake-id-token','access_token':'fake-token'}), \
                patch.object(self.module.google,'parse_id_token',return_value=info) as parse:
            result = self.get(f'/auth/callback?code=fake-code&state={state}')
        self.assertEqual(result.status_code,302)
        self.assertIn('nonce',parse.call_args.kwargs)
        self.assertTrue(parse.call_args.kwargs['nonce'])
        self.assertNotEqual(self.token(),old)
        self.assertEqual(self.get('/me').json['display_name'],'OAuth User')
        with patch.object(self.module.google,'fetch_access_token') as exchange:
            self.assertEqual(self.get(f'/auth/callback?code=fake-code&state={state}').status_code,400)
        exchange.assert_not_called()

    def test_oauth_unverified_email_cannot_create_or_link_account(self):
        for verified in (False,None):
            state = self.oauth_start()
            info = {'email':'user1@example.test','sub':'fake-sub','email_verified':verified}
            with patch.object(self.module.google,'fetch_access_token',return_value={'userinfo':info,'access_token':'fake-token'}):
                self.assertEqual(self.get(f'/auth/callback?code=fake-code&state={state}').status_code,400)
        self.assertIsNone(self.conn.execute('SELECT google_id FROM users WHERE id=1').fetchone()[0])

    def test_oauth_provider_denial_handled_without_write(self):
        self.oauth_start()
        self.assertEqual(self.get('/auth/callback?error=access_denied').status_code,400)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],3)

    def test_malformed_csrf_header_rejected(self):
        for value in ('x'*2049,'é',''):
            self.assertEqual(self.post('/logout',headers={'X-CSRF-Token':value}).status_code,400)

    def test_future_json_methods_require_csrf(self):
        isolated = Flask('csrf_test_fixture')
        isolated.secret_key='fake-isolated-test-key'
        init_web_security(isolated,lambda: self.fail('Anonymous CSRF checks need no DB'))
        @isolated.route('/mutate',methods=['POST','PUT','PATCH','DELETE'])
        def mutate():
            return {'ok':True}
        client = isolated.test_client()
        token = client.get('/csrf-token').json['csrf_token']
        for method in ('POST','PUT','PATCH','DELETE'):
            self.assertEqual(client.open('/mutate',method=method,json={}).status_code,400)
            self.assertEqual(client.open('/mutate',method=method,json={},headers={'X-CSRF-Token':token}).status_code,200)

    def test_duplicate_form_tokens_and_invalid_header_cannot_bypass(self):
        token = self.token()
        self.assertEqual(self.post('/logout',data=MultiDict([('csrf_token',token),('csrf_token',token)])).status_code,400)
        self.assertEqual(self.post('/logout',data={'csrf_token':token},headers={'X-CSRF-Token':'bad'}).status_code,400)

    def test_csrf_rejected_upload_creates_no_file_or_track(self):
        self.session_user(1)
        before = set(Path(self.temp.name).iterdir())
        result = self.post('/tracks',data={'file':(io.BytesIO(b'fake'),'blocked.mp3'),
            'title':'Blocked','genre_id':'1'},content_type='multipart/form-data')
        self.assertEqual(result.status_code,400)
        self.assertEqual(set(Path(self.temp.name).iterdir()),before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)

    def test_oauth_existing_account_link_preserved(self):
        state = self.oauth_start()
        info = {'email':'user1@example.test','sub':'fake-existing-sub','email_verified':True}
        with patch.object(self.module.google,'fetch_access_token',return_value={'access_token':'fake-token'}), \
                patch.object(self.module.google,'userinfo',return_value=info):
            self.assertEqual(self.get(f'/auth/callback?code=fake-code&state={state}').status_code,302)
        self.assertEqual(self.get('/me').json['user_id'],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM users').fetchone()[0],3)
        self.assertEqual(self.conn.execute('SELECT google_id FROM users WHERE id=1').fetchone()[0],'fake-existing-sub')


if __name__=='__main__':
    unittest.main()
