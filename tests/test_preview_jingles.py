"""Station IDs use temporary files/catalogue and bounded synthetic FFmpeg only."""
import json
from pathlib import Path
import random
import sqlite3
import unittest

import private_radio as radio
import preview_jingles
import test_private_radio as fixture


class JingleTests(unittest.TestCase):
    setUpClass = classmethod(fixture.PrivateRadioTests.setUpClass.__func__)
    setUp = fixture.PrivateRadioTests.setUp
    imported = fixture.PrivateRadioTests.imported

    def prepare(self):
        folder = preview_jingles.directory(self.root)
        folder.mkdir(parents=True)
        for name in ('one.mp3', 'two.mp3'):
            (folder/name).write_bytes(self.audio)
        (folder/'.DS_Store').write_bytes(b'ignore')
        (folder/'invalid.mp3').write_bytes(b'not an MP3')
        return folder

    def test_scan_valid_invalid_non_audio_and_symlinks(self):
        folder = self.prepare()
        (folder/'link.mp3').symlink_to(folder/'one.mp3')
        items = preview_jingles.scan(self.root)
        self.assertEqual({i['name'] for i in items}, {'one.mp3','two.mp3','invalid.mp3','link.mp3'})
        self.assertEqual({i['name'] for i in items if i['valid']}, {'one.mp3','two.mp3'})
        self.assertTrue(all(i['duration'] > 0 for i in items if i['valid']))

    def test_random_choice_avoids_adjacent_duplicates_and_handles_empty_single(self):
        self.prepare()
        picker = preview_jingles.JinglePicker(preview_jingles.scan(self.root), rng=random.Random(7))
        names = [picker.choose()['name'] for _ in range(20)]
        self.assertTrue(all(a != b for a,b in zip(names,names[1:])))
        self.assertIsNone(preview_jingles.JinglePicker([]).choose())
        self.assertIsNone(preview_jingles.JinglePicker([{'valid':False}]).choose())
        one = preview_jingles.JinglePicker([{'valid':True,'name':'one'}])
        self.assertEqual(one.choose()['name'], one.choose()['name'])
        self.assertEqual(preview_jingles.scan(self.root/'absent'), [])

    def test_real_track_jingle_track_preserves_next_and_continuous_encoder(self):
        self.imported()
        self.prepare()
        worker = radio.load_worker(self.root)
        states = []
        original = worker.write_state
        def record(*args, **kwargs):
            original(*args, **kwargs)
            states.append(json.loads((self.root/'hls/now.json').read_text()))
        worker.write_state = record
        pids = []
        class FiveItems(worker.Engine):
            count = 0
            def play(self, path):
                pids.append(self.encoder.pid)
                result = super().play(path)
                self.count += 1
                if self.count == 5:
                    worker.STOP = True
                return result
        engine = FiveItems(threads=1, continuous=True)
        radio.play_loop(self.root, worker, seconds=25, engine=engine)
        items = states[:-1]
        self.assertEqual([i['type'] for i in items], ['track','jingle','track','jingle','track'])
        self.assertEqual(len(set(pids)), 1)
        for index in (0, 2):
            before, station, after = items[index:index+3]
            self.assertEqual(before['next_track_id'], station['next_track_id'])
            self.assertEqual(station['next_track_id'], after['track_id'])
            self.assertEqual(station['title'], 'Digger Radio')
            self.assertEqual(station['artist'], 'Station ID')
            self.assertIsNone(station['track_id'])
        self.assertNotEqual(items[1]['jingle_name'], items[3]['jingle_name'])
        with sqlite3.connect(self.root/'catalogue.sqlite') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM private_tracks').fetchone()[0], 2)
            self.assertEqual(conn.execute('SELECT count(*) FROM private_plays').fetchone()[0], 3)
        timeline = json.loads((self.root/'hls/timeline.json').read_text())
        self.assertEqual([i['type'] for i in timeline['tracks']], [i['type'] for i in items])
        self.assertIn('#EXT-X-PROGRAM-DATE-TIME:', (self.root/'hls/stream.m3u8').read_text())
        self.assertIsNone(states[-1]['type'])
        self.assertIsNone(engine.encoder)
