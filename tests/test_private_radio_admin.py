"""No real catalogue, audio, network sockets or public app touched."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

import private_radio as radio
import private_radio_admin as admin


class PrivateCatalogueAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'radio'
        radio.initialize(self.root)
        self.audio = b'synthetic test placeholder, never decoded'
        for name in ('one.mp3', 'two.mp3', 'excluded.mp3'):
            (self.root/name).write_bytes(self.audio)
        conn = radio.connect(self.root)
        with conn:
            for index, name in enumerate(('one.mp3','two.mp3')):
                conn.execute('''INSERT INTO private_tracks(position,filename,title,artist,sha256,size,duration,
                    private_rights_confirmed) VALUES (?,?,?,?,?,?,?,1)''',
                    (index,name,f'Title {index}','Artist',hashlib.sha256(self.audio).hexdigest(),len(self.audio),125.5))
        conn.close()
        self.key = (self.root/'access.key').read_bytes()
        admin.migrate(self.root)
        self.app = admin.create_app(self.root)
        self.app.testing = True
        self.client = self.app.test_client()
        token = base64.b64encode(radio.USERNAME.encode()+b':'+self.key).decode()
        self.auth = {'Authorization':'Basic '+token}
        self.base = 'http://127.0.0.1:17002'

    def get(self, path='/', **kwargs):
        return self.client.get(path,base_url=self.base,headers=self.auth,**kwargs)

    def post(self, action='metadata', revision=0, **kwargs):
        page = self.get()
        token = re.search(rb'name="csrf_token" value="([^"]+)"',page.data)[1].decode()
        data = dict(csrf_token=token,action=action,revision=str(revision),title='Corrected',artist='Artist corrected')
        data.update(kwargs)
        return self.client.post('/tracks/1',base_url=self.base,headers=self.auth,data=data)

    def rows(self):
        return admin.listing(self.root)[0]

    def test_additive_migration_idempotent_preserves_tracks_history_and_secret(self):
        conn = radio.connect(self.root)
        with conn:
            conn.execute('INSERT INTO private_plays(track_id) VALUES (1)')
        conn.close()
        admin.migrate(self.root)
        self.assertEqual(len(self.rows()),2)
        self.assertTrue(all(t['enabled']==1 and t['revision']==0 for t in self.rows()))
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_plays').fetchone()[0],1)
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],1)
        self.assertEqual((self.root/'access.key').read_bytes(),self.key)
        for name in ('one.mp3','two.mp3'): self.assertEqual((self.root/name).read_bytes(),self.audio)

    def test_auth_host_csrf_origins_and_private_file_denials(self):
        for path in ('/','/one.mp3','/catalogue.sqlite','/access.key','/hls/stream.m3u8','/../access.key'):
            self.assertEqual(self.client.get(path,base_url=self.base).status_code,401)
            if path != '/': self.assertEqual(self.get(path).status_code,404)
        self.assertEqual(self.client.get('/',base_url='http://evil.test',headers=self.auth).status_code,400)
        self.assertEqual(self.client.post('/tracks/1',base_url=self.base,headers=self.auth,data={'action':'remove'}).status_code,403)
        page = self.get()
        token = re.search(rb'name="csrf_token" value="([^"]+)"',page.data)[1].decode()
        headers = dict(self.auth,Origin='https://evil.test')
        self.assertEqual(self.client.post('/tracks/1',base_url=self.base,headers=headers,
                                         data={'csrf_token':token}).status_code,403)
        self.assertIn("frame-ancestors 'none'",page.headers['Content-Security-Policy'])
        self.assertEqual(page.headers['Cache-Control'],'no-store')
        self.assertNotIn(self.key,page.data)
        self.assertEqual(self.rows()[0]['revision'],0)

    def test_listing_separates_unimported_files_and_escapes_metadata(self):
        self.assertEqual(self.post(title='<script>alert(1)</script>').status_code,303)
        page = self.get()
        self.assertIn(b'&lt;script&gt;',page.data)
        self.assertNotIn(b'<script>',page.data)
        self.assertIn(b'2:05',page.data)
        self.assertIn(b'excluded.mp3',page.data)
        self.assertEqual([x['name'] for x in admin.listing(self.root)[1]],['excluded.mp3'])
        self.assertEqual((self.root/'one.mp3').read_bytes(),self.audio)
        self.assertEqual(self.post(revision=0,title='Stale').status_code,409)
        self.assertEqual(self.rows()[0]['title'],'<script>alert(1)</script>')

    def test_unimported_symlinks_are_inventory_only_and_never_followed(self):
        (self.root/'link.mp3').symlink_to(self.root/'access.key')
        extras = {item['name']: item for item in admin.listing(self.root)[1]}
        self.assertFalse(extras['link.mp3']['regular'])
        self.assertEqual(self.get('/link.mp3').status_code,404)
        self.assertNotIn(self.key,self.get().data)

    def test_ephemeral_launcher_is_bounded_loopback_only_and_closes(self):
        from unittest.mock import MagicMock
        server = MagicMock()
        server.__enter__.return_value = server
        with patch.object(radio,'ROOT',self.root), patch('sys.argv',['private_radio_admin','--seconds','1']), \
                patch.object(admin,'make_server',return_value=server) as bind, \
                patch.object(admin.signal,'signal'), patch.object(admin.time,'monotonic',side_effect=[10,10,12]):
            admin.main()
        self.assertEqual(bind.call_args.args[:2],('127.0.0.1',17002))
        server.handle_request.assert_called_once()
        server.__exit__.assert_called_once()
        self.assertFalse(self.app.debug)

    def test_toggle_remove_confirmation_keep_audio_and_history_and_skip_programming(self):
        self.assertEqual(self.post(action='toggle').status_code,303)
        with patch.object(radio,'validate_file'):
            self.assertEqual([t['id'] for t in radio.tracks(self.root)],[2])
        self.assertEqual(self.post(action='toggle',revision=1).status_code,303)
        self.assertEqual(self.post(action='remove',revision=2).status_code,409)
        self.assertEqual(self.post(action='remove',revision=2,confirm='1').status_code,303)
        row = self.rows()[0]
        self.assertIsNotNone(row['removed_at'])
        self.assertEqual(row['enabled'],0)
        self.assertEqual(self.post(action='toggle',revision=3).status_code,409)
        self.assertEqual((self.root/'one.mp3').read_bytes(),self.audio)
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_catalogue_events').fetchone()[0],3)
        with patch.object(radio,'validate_file'):
            self.assertEqual([t['id'] for t in radio.tracks(self.root)],[2])
        admin.change(self.root,2,0,'toggle')
        with patch.object(radio,'validate_file'), self.assertRaises(ValueError): radio.tracks(self.root)

    def test_playback_lock_allows_read_but_refuses_all_writes(self):
        with radio.catalogue_lock(self.root):
            self.assertEqual(self.get().status_code,200)
            for action in ('metadata','toggle','remove'):
                self.assertEqual(self.post(action=action,confirm='1').status_code,409)
        self.assertEqual(self.rows()[0]['revision'],0)
        self.assertEqual(self.post().status_code,303)

    def test_migration_refuses_active_radio_and_rolls_back_partial_failure(self):
        fresh = Path(self.temp.name)/'fresh'
        radio.initialize(fresh)
        with radio.catalogue_lock(fresh), self.assertRaises(ValueError): admin.migrate(fresh)
        conn = radio.connect(fresh)
        self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],0)
        # Simulate an incompatible partial schema: no additional partial changes survive.
        conn.execute('ALTER TABLE private_tracks ADD COLUMN removed_at TEXT')
        conn.close()
        with self.assertRaises(sqlite3.OperationalError): admin.migrate(fresh)
        with sqlite3.connect(fresh/'catalogue.sqlite') as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(private_tracks)')}
            self.assertNotIn('enabled',columns)
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],0)

    def test_run_acquires_lock_before_playlist_snapshot(self):
        def validate(_):
            with self.assertRaises(ValueError), radio.catalogue_lock(self.root): pass
            raise ValueError('stop before opening a socket')
        with patch.object(radio,'tracks',side_effect=validate), self.assertRaises(ValueError):
            radio.run(self.root,1)
        with radio.catalogue_lock(self.root): pass

    def test_play_loop_uses_corrected_active_tracks_and_preserves_history(self):
        admin.change(self.root,1,0,'metadata',title='Updated title',artist='Updated artist')
        admin.change(self.root,2,0,'toggle')
        recorded=[]
        worker=types.SimpleNamespace(STOP=False,clear_hls_dir=lambda:None,
            write_state=lambda *args,**kw:recorded.append((args,kw)))
        class Engine:
            def start(self): pass
            def play(self,path):
                self.path=path
                worker.STOP=True
                return True
            def stop(self): pass
        engine=Engine()
        with patch.object(radio,'validate_file'):
            radio.play_loop(self.root,worker,seconds=2,engine=engine)
        self.assertEqual(engine.path,str(self.root/'one.mp3'))
        self.assertEqual(recorded[0][0][0]['title'],'Updated title')
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            rows=conn.execute('SELECT track_id,ended_at FROM private_plays').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],1)
        self.assertIsNotNone(rows[0][1])

    def test_invalid_input_does_not_change_catalogue(self):
        for kwargs in ({'title':''},{'artist':'bad\nname'},{'action':'import'},{'revision':'bad'}):
            self.assertEqual(self.post(**kwargs).status_code,409)
        self.assertEqual(self.rows()[0]['revision'],0)
        with self.assertRaises(ValueError): admin.change(self.root,2**80,0,'toggle')


if __name__=='__main__': unittest.main()
