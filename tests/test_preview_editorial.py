import hashlib
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

import private_radio as radio
import private_radio_admin as old_admin
import preview_editorial as editorial
from preview_schedule import ShuffleCycle


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'radio'
        radio.initialize(self.root)
        old_admin.migrate(self.root)
        conn = radio.connect(self.root)
        with conn:
            for tid in range(1,5):
                raw = b'fixture-audio-'+str(tid).encode()
                name = f'{tid}.mp3'
                (self.root/name).write_bytes(raw)
                conn.execute('''INSERT INTO private_tracks(id,position,filename,title,artist,sha256,size,duration,private_rights_confirmed)
                    VALUES(?,?,?,?,?,?,?,?,1)''',(tid,tid,name,f'Title {tid}',f'Artist {tid}',hashlib.sha256(raw).hexdigest(),len(raw),100))
            conn.execute('INSERT INTO private_plays(track_id) VALUES(1)')
        conn.close()

    def rows(self):
        return editorial.listing(self.root)

    def test_additive_migration_idempotent_constraints_and_preservation(self):
        before = self.rows()
        editorial.migrate(self.root)
        editorial.migrate(self.root)
        after = self.rows()
        self.assertEqual([{k:r[k] for k in before[0]} for r in after], before)
        self.assertTrue(all(r['genre']=='' and r['year'] is None and r['rejection_reason'] is None for r in after))
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('pragma user_version').fetchone()[0],2)
            self.assertEqual(conn.execute('select count(*) from private_plays').fetchone()[0],1)
            for sql in ('UPDATE private_tracks SET year=999','UPDATE private_tracks SET rejection_reason="invalid"'):
                with self.assertRaises(sqlite3.IntegrityError):conn.execute(sql)

    def test_metadata_status_audit_and_no_audio_writes_even_during_radio_lock(self):
        editorial.migrate(self.root)
        before = self.rows()[0]
        audio = (self.root/before['filename']).read_bytes()
        with radio.catalogue_lock(self.root):
            editorial.change(self.root,1,0,'metadata',title='New title',artist='New artist',genre='Soul',year='2026')
            editorial.change(self.root,1,1,'remove',confirm='1')
            self.assertEqual(editorial.status(self.rows()[0]),'removed')
            editorial.change(self.root,1,2,'restore')
            editorial.change(self.root,1,3,'reject',reason='doublon',confirm='1')
        after = self.rows()[0]
        self.assertEqual(after['id'],before['id'])
        self.assertEqual((after['title'],after['artist'],after['genre'],after['year']),('New title','New artist','Soul',2026))
        self.assertEqual(after['filename'],before['filename'])
        self.assertEqual((self.root/after['filename']).read_bytes(),audio)
        self.assertEqual(editorial.status(after),'rejected')
        with self.assertRaises(ValueError):editorial.change(self.root,1,4,'restore')
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            events=conn.execute('select track_id,action,before_json,after_json,created_at from private_catalogue_events order by id').fetchall()
        self.assertEqual([r[1] for r in events],['metadata','remove','restore','reject'])
        self.assertTrue(all(r[0]==1 and r[4] for r in events))
        self.assertEqual(json.loads(events[0][2])['title'],'Title 1')
        self.assertEqual(json.loads(events[-1][3])['rejection_reason'],'doublon')
        for kwargs in ({'reason':None},{'reason':'bad'},{'reason':'doublon','confirm':None}):
            with self.assertRaises(ValueError):editorial.change(self.root,2,0,'reject',**kwargs)
        with self.assertRaises(ValueError):editorial.change(self.root,1,0,'remove',confirm='1')

    def test_search_filters_and_schedule_exclusion_recalculation(self):
        editorial.migrate(self.root)
        schedule=ShuffleCycle(self.rows(),rng=random.Random(4))
        current, old_next=schedule.current, schedule.next_track
        editorial.change(self.root,old_next['id'],0,'reject',reason='droits_incomplets',confirm=str(old_next['id']))
        editorial.change(self.root,current['id'],0,'remove',confirm=str(current['id']))
        active=[r for r in self.rows() if editorial.eligible(r)]
        schedule.refresh(active)
        self.assertEqual(schedule.current['id'],current['id'],'playing track is not replaced')
        self.assertNotIn(schedule.next_track['id'],(current['id'],old_next['id']))
        reserved=schedule.next_track['id']
        schedule.advance()
        self.assertEqual(schedule.current['id'],reserved)
        for _ in range(20):
            self.assertIn(schedule.current['id'],{r['id'] for r in active})
            schedule.refresh(active);schedule.advance()
        self.assertEqual(len(editorial.listing(self.root,state='removed')),1)
        self.assertEqual(len(editorial.listing(self.root,state='rejected')),1)
        self.assertEqual(len(editorial.listing(self.root,state='active')),2)
        self.assertEqual(editorial.listing(self.root,query='aRTIST 3')[0]['id'],3)
        self.assertEqual(editorial.listing(self.root,query='Title 4')[0]['id'],4)

    def test_removing_reservation_at_cycle_boundary_avoids_immediate_repeat(self):
        rows=[dict(id=i) for i in (1,2,3)]
        schedule=ShuffleCycle(rows,rng=random.Random(2))
        # Track 3 finishes the old cycle; 1 is reserved from the new cycle.
        schedule.current=rows[2]
        schedule.next_track=rows[0]
        schedule.remaining=[rows[2],rows[1]]
        schedule.drawn={1}
        schedule.refresh(rows[1:])
        self.assertEqual(schedule.current['id'],3)
        self.assertEqual(schedule.next_track['id'],2)
        self.assertEqual([r['id'] for r in schedule.remaining],[3])

    def test_engine_updates_reserved_next_during_track_and_jingle_without_cutting(self):
        editorial.migrate(self.root)
        played=[]; next_ids=[]; current_done=[]
        worker=radio.load_worker(self.root)
        owner=self
        class Engine:
            timeline_callback=None
            control_callback=None
            pending_track=None
            def start(self):pass
            def stop(self):pass
            def play(self,path):
                data=json.loads((owner.root/'hls/now.json').read_text())
                played.append(data['type'])
                if len(played)==1:
                    first=data['track_id']; withdrawn=data['next_track_id']
                    editorial.change(owner.root,withdrawn,0,'remove',confirm=str(withdrawn))
                    editorial.change(owner.root,first,0,'remove',confirm=str(first))
                    self.control_callback()
                    changed=json.loads((owner.root/'hls/now.json').read_text())
                    owner.assertEqual(changed['track_id'],first)
                    owner.assertNotEqual(changed['next_track_id'],withdrawn)
                    next_ids.append(changed['next_track_id'])
                    current_done.append(first)
                elif data['type']=='jingle':
                    owner.assertEqual(data['next_track_id'],next_ids[-1])
                    tid=data['next_track_id']
                    editorial.change(owner.root,tid,0,'reject',reason='doublon',confirm=str(tid))
                    self.control_callback()
                    changed=json.loads((owner.root/'hls/now.json').read_text())
                    owner.assertEqual(changed['type'],'jingle')
                    owner.assertNotEqual(changed['next_track_id'],tid)
                    next_ids.append(changed['next_track_id'])
                else:
                    owner.assertEqual(data['track_id'],next_ids[-1])
                    worker.STOP=True
                return True
        with patch.object(radio,'validate_file'), patch.object(radio.preview_jingles,'scan',return_value=[dict(name='station.mp3',valid=True,duration=1,sha256='fake',size=1)]):
            radio.play_loop(self.root,worker,seconds=5,engine=Engine())
        self.assertEqual(played,['track','jingle','track'])
        self.assertEqual(len(current_done),1)

    def test_empty_future_stops_cleanly_after_current_finishes(self):
        editorial.migrate(self.root)
        worker=radio.load_worker(self.root);finished=[];owner=self
        class Engine:
            control_callback=None
            def start(self):pass
            def stop(self):finished.append('stop')
            def play(self,path):
                for row in owner.rows():editorial.change(owner.root,row['id'],row['revision'],'remove',confirm=str(row['id']))
                self.control_callback()
                finished.append('current finished')
                return True
        with patch.object(radio,'validate_file'):
            radio.play_loop(self.root,worker,seconds=5,engine=Engine())
        self.assertEqual(finished,['current finished','stop'])
