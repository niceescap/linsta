import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from dev_demo_gateway import COOKIE, DemoGateway


class DemoSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/'grant.json'
        self.token='a'*43
        self.grant={'enabled':True,'host':'test.trycloudflare.com','expires':200,
                    'digest':hashlib.sha256(self.token.encode()).hexdigest()}
        self.state.write_text(json.dumps(self.grant))
        self.calls=[]
        def inner(env,start):
            self.calls.append(env)
            start('200 OK',[('Set-Cookie','admin=bad'),('Content-Type','text/plain')])
            return [b'protected']
        self.gateway=DemoGateway(inner,self.state,clock=lambda:100)

    def request(self,path='/',method='GET',token=None,body=b'',**headers):
        env={'PATH_INFO':path,'REQUEST_METHOD':method,'HTTP_HOST':self.grant['host'],
             'wsgi.input':io.BytesIO(body),'CONTENT_LENGTH':str(len(body)),**headers}
        if token: env['HTTP_COOKIE']=f'{COOKIE}={token}; digger_dev_session=forged'
        result={}
        def start(status,headers,exc=None): result.update(status=int(status[:3]),headers=dict(headers))
        result['body']=b''.join(self.gateway(env,start))
        return result

    def test_full_day_from_activation_cookie_and_absolute_expiry(self):
        from unittest.mock import patch
        import dev_demo_share
        with patch('dev_demo_share.time.time',return_value=1000.25):
            grant=dev_demo_share.activate(self.token,self.grant['host'])
        self.assertEqual(grant['activated_at'],1000.25)
        self.assertEqual(grant['expires'],87400.25)
        self.state.write_text(json.dumps(grant))
        self.gateway.clock=lambda:1000.25
        r=self.request('/demo-access','POST',body=self.token.encode(),
                       HTTP_ORIGIN='https://'+self.grant['host'])
        self.assertIn('Max-Age=86400',r['headers']['Set-Cookie'])
        self.gateway.clock=lambda:grant['expires']-.1
        self.assertEqual(self.request(token=self.token)['status'],200)
        r=self.request('/demo-access','POST',body=self.token.encode(),
                       HTTP_ORIGIN='https://'+self.grant['host'])
        self.assertIn('Max-Age=1',r['headers']['Set-Cookie'])
        self.gateway.clock=lambda:grant['expires']
        self.assertEqual(self.request(token=self.token)['status'],410)
        self.gateway.clock=lambda:grant['expires']+1
        self.assertEqual(self.request('/demo-access','POST',body=self.token.encode(),
                         HTTP_ORIGIN='https://'+self.grant['host'])['status'],410)

    def test_anonymous_media_blocked(self):
        for p in ['/preview-radio/hls/stream.m3u8','/preview-radio/hls/seg_123.ts',
                  '/preview-radio/now-playing','/preview-radio/tracks/4/cover','/morceaux/4']:
            self.assertEqual(self.request(p)['status'],401)
        self.assertEqual(self.calls,[])

    def test_cookie_exchange_requires_valid_token_and_origin(self):
        self.assertEqual(self.request('/demo-access','POST',body=self.token.encode())['status'],403)
        r=self.request('/demo-access','POST',body=self.token.encode(),HTTP_ORIGIN='https://'+self.grant['host'])
        self.assertEqual(r['status'],204)
        for flag in ['Secure','HttpOnly','SameSite=Strict','Max-Age=100']:
            self.assertIn(flag,r['headers']['Set-Cookie'])

    def test_guest_cannot_use_admin_writes_raw_audio_or_auth(self):
        for p in ['/admin','/admin/preview-radio','/submissions','/login','/connexion',
                  '/csrf-token','/preview-radio/tracks/4/audio','/.env','/hls/stream.m3u8']:
            self.assertEqual(self.request(p,token=self.token)['status'],403)
        for m in ['POST','PUT','PATCH','DELETE','OPTIONS']:
            self.assertEqual(self.request('/',m,token=self.token)['status'],403)
        self.assertEqual(self.calls,[])

    def test_identity_is_never_forwarded_and_cookies_not_leaked(self):
        r=self.request('/preview-radio/hls/seg_123.ts',token=self.token)
        self.assertEqual(r['status'],200)
        self.assertNotIn('Set-Cookie',r['headers'])
        self.assertEqual(self.calls[0]['HTTP_COOKIE'],'')
        self.assertTrue(self.calls[0]['DIGGER_DEMO_VERIFIED'])

    def test_revoked_expired_bad_cookie_host(self):
        self.assertEqual(self.request(token='bad')['body'].startswith(b'<!doctype'),True)
        self.assertEqual(self.request(token=self.token,HTTP_HOST='evil.test')['status'],403)
        for change in [{'enabled':False},{'expires':99}]:
            self.state.write_text(json.dumps(dict(self.grant,**change)))
            self.assertEqual(self.request(token=self.token)['status'],410)


if __name__=='__main__': unittest.main()
