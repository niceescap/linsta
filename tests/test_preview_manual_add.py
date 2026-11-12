"""Private admin uploads use generated names, FFmpeg validation and temporary catalogues."""
import hashlib
import io
import json
import math
import sqlite3
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage

import private_radio as radio
import private_radio_admin
import preview_editorial
import preview_manual_add as manual
from preview_schedule import ShuffleCycle
from test_media_storage import cover_bytes


def wav_bytes(seconds=1.1):
    out=io.BytesIO()
    with wave.open(out,'wb') as target:
        target.setnchannels(1); target.setsampwidth(2); target.setframerate(22050)
        frames=int(seconds*22050)
        target.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(2*math.pi*440*i/22050)))
                                    for i in range(frames)))
    return out.getvalue()


def fields(state='active', **extra):
    result=dict(title='Manual song',artist='Test artist',genre='Soul',year='2026',
        source_platform='youtube',source_url='https://youtube.com/watch?v=abcdefghijk',
        internal_note='admin test',initial_status=state,private_preview_confirmed='on')
    result.update(extra)
    return result


def upload(raw, name):
    return FileStorage(stream=io.BytesIO(raw),filename=name)


class ManualAddTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'private-radio'
        radio.initialize(self.root)
        private_radio_admin.migrate(self.root)
        preview_editorial.migrate(self.root)
        self.wav=wav_bytes()
        self.limits=__import__('audio_storage').MediaLimits(audio_bytes=2*1024*1024,
            cover_bytes=1024*1024,duration_seconds=60,cover_pixels=100_000,cover_dimension=512)

    def add(self, raw, name, *, state='active', cover=None, **kwargs):
        return manual.add_track(self.root,fields(state,**kwargs),io.BytesIO(raw),
            upload(cover,'art.png') if cover is not None else None,limits=self.limits)

    def test_migration_v2_to_v3_keeps_existing_catalogue_and_adds_constraints(self):
        # Test the additive v2 boundary independently of the v1-to-v2 fixture.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'radio'; radio.initialize(root); private_radio_admin.migrate(root)
            conn=radio.connect(root)
            conn.executescript(preview_editorial.MIGRATION)
            conn.close()
            preview_editorial.migrate(root)
            with sqlite3.connect(root/'catalogue.sqlite') as conn:
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],3)
                names={r[1] for r in conn.execute('PRAGMA table_info(private_tracks)')}
                self.assertTrue({'cover_path','source_platform','source_url','internal_note'}<=names)
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("INSERT INTO private_tracks(position,filename,title,artist,sha256,size,duration,private_rights_confirmed,source_platform) VALUES (1,'x.mp3','x','y','0',1,1,1,'invalid')")

    def test_mp3_and_wav_detect_duration_hash_and_generated_names(self):
        mp3=Path(self.root.parent/'synth.mp3')
        import subprocess
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=550:duration=1.2',
            '-threads','1','-codec:a','libmp3lame','-y',str(mp3)],check=True,timeout=15)
        mp3raw=mp3.read_bytes()
        rows=[self.add(mp3raw,'../../browser.mp3'),self.add(self.wav,'client name.wav')]
        self.assertTrue(rows[0]['filename'].startswith('digger-manual-') and rows[0]['filename'].endswith('.mp3'))
        self.assertTrue(rows[1]['filename'].startswith('digger-manual-') and rows[1]['filename'].endswith('.wav'))
        for row,raw in zip(rows,(mp3raw,self.wav)):
            self.assertEqual(row['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertAlmostEqual(row['duration'],radio.validate_file(self.root,row['filename'])[2],places=2)
            self.assertGreater(row['duration'],1)
        self.assertEqual(rows[0]['title'],'Manual song')
        self.assertEqual(rows[0]['source_platform'],'youtube')
        self.assertEqual(rows[0]['internal_note'],'admin test')

    def test_cover_active_removed_and_shuffle_eligibility(self):
        active=self.add(self.wav,'wave.wav',cover=cover_bytes())
        removed=self.add(wav_bytes(1.3),'wave2.wav',state='removed')
        self.assertEqual(active['enabled'],1); self.assertTrue(active['cover_path'].startswith('covers/digger-cover-'))
        with Image.open(self.root/active['cover_path']) as image:
            image.load(); self.assertEqual(image.format,'PNG')
        self.assertIsNone(removed['cover_path']); self.assertEqual(removed['enabled'],0)
        active_rows=[r for r in preview_editorial.listing(self.root) if preview_editorial.eligible(r)]
        self.assertEqual([r['id'] for r in active_rows],[active['id']])
        self.assertEqual(ShuffleCycle(active_rows).current['id'],active['id'])
        self.assertNotIn(removed['id'],[r['id'] for r in active_rows])

    def test_new_active_track_joins_schedule_without_changing_reserved_next(self):
        first=self.add(self.wav,'first.wav')
        schedule=ShuffleCycle([first])
        current_id=schedule.current['id']; next_id=schedule.next_track['id']
        second=self.add(wav_bytes(1.25),'second.wav')
        active=[r for r in preview_editorial.listing(self.root) if preview_editorial.eligible(r)]
        schedule.refresh(active)
        self.assertEqual(schedule.current['id'],current_id)
        self.assertEqual(schedule.next_track['id'],next_id)
        self.assertIn(second['id'],[r['id'] for r in schedule.remaining])
        schedule.advance()
        self.assertEqual(schedule.current['id'],next_id)
        self.assertEqual(schedule.next_track['id'],second['id'])

    def test_duplicate_invalid_audio_and_invalid_cover_leave_no_row_or_file(self):
        first=self.add(self.wav,'first.wav')
        generated=lambda:sorted(p.name for p in self.root.iterdir() if p.name.startswith('digger-manual-'))
        files_before=generated()
        with self.assertRaises(manual.DuplicateTrack): self.add(self.wav,'different-name.wav')
        with self.assertRaises(ValueError): self.add(b'not audio','bad.mp3')
        with self.assertRaises(Exception): self.add(wav_bytes(1.2),'cover-invalid.wav',cover=b'<svg/>')
        self.assertEqual(generated(),files_before)
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_tracks').fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM private_catalogue_events WHERE action='manual_admin_add'").fetchone()[0],1)
        self.assertEqual(list((self.root/'tracks'/'.incoming').iterdir()),[])
        self.assertTrue((self.root/first['filename']).exists())

    def test_sql_failure_rolls_back_db_and_published_files(self):
        real_connect=preview_editorial.connect
        class Faulty:
            def __init__(self,conn): self.conn=conn
            def execute(self,sql,*args):
                if sql.lstrip().startswith('INSERT INTO private_tracks'):
                    raise sqlite3.OperationalError('injected test failure')
                return self.conn.execute(sql,*args)
            def __getattr__(self,name): return getattr(self.conn,name)
        with patch.object(preview_editorial,'connect',side_effect=lambda *a,**k:Faulty(real_connect(*a,**k))):
            with self.assertRaises(sqlite3.OperationalError): self.add(self.wav,'test.wav')
        self.assertEqual(list(self.root.glob('digger-manual-*')),[])
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM private_tracks').fetchone()[0],0)

    def test_replacement_cover_and_editorial_edit_keep_audio_identity(self):
        row=self.add(self.wav,'wave.wav')
        original_audio=(self.root/row['filename']).read_bytes()
        cover_path,session=manual.prepare_cover_replacement(self.root,upload(cover_bytes('JPEG'),'new.jpg'),limits=self.limits)
        preview_editorial.change(self.root,row['id'],row['revision'],'metadata',title='Edited',artist='Artist 2',
            genre='Jazz',year='2025',source_platform='spotify',source_url='https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC',
            internal_note='new note',cover_path=cover_path)
        manual.finish_cover_replacement(session,keep=True)
        updated=preview_editorial.listing(self.root)[0]
        self.assertEqual(updated['id'],row['id']); self.assertEqual(updated['filename'],row['filename'])
        self.assertEqual((self.root/row['filename']).read_bytes(),original_audio)
        self.assertEqual((updated['title'],updated['source_platform'],updated['internal_note']),('Edited','spotify','new note'))
        self.assertTrue((self.root/updated['cover_path']).exists())

    def test_wav_enters_the_real_continuous_hls_engine(self):
        row=self.add(wav_bytes(5.2),'wav.wav')
        worker=radio.load_worker(self.root)
        class OneWav(worker.Engine):
            def play(inner,path):
                self.assertTrue(path.endswith('.wav'))
                result=super(OneWav,inner).play(path)
                worker.STOP=True
                return result
        radio.play_loop(self.root,worker,seconds=10,engine=OneWav(threads=1,continuous=True))
        playlist=(self.root/'hls/stream.m3u8').read_text()
        segments=[line for line in playlist.splitlines() if line.endswith('.ts')]
        self.assertTrue(segments,playlist)
        self.assertTrue((self.root/'hls'/segments[-1]).stat().st_size>0)
        timeline=json.loads((self.root/'hls/timeline.json').read_text())
        self.assertEqual(timeline['tracks'][0]['id'],row['id'])
        self.assertEqual(timeline['tracks'][0]['type'],'track')


if __name__=='__main__': unittest.main()
