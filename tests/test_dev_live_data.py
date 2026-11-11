"""Published HLS + emitted PCM timeline; no production files or services."""
import json
import time
from datetime import datetime, timezone
import unittest
from test_preview_radio_web import PreviewRoutesTests


class LiveDataTests(unittest.TestCase):
    setUpClass = classmethod(PreviewRoutesTests.setUpClass.__func__)
    get = PreviewRoutesTests.get
    session_user = PreviewRoutesTests.session_user
    publish = PreviewRoutesTests.publish

    def setUp(self):
        PreviewRoutesTests.setUp(self)
        old = self.app.config.get('EDITORIAL_LIVE_DATA')
        self.app.config['EDITORIAL_LIVE_DATA'] = True
        self.addCleanup(self.app.config.__setitem__, 'EDITORIAL_LIVE_DATA', old)
        self.session_user(1)
        self.started = self.publish()
        self.origin = self.started
        date = datetime.fromtimestamp(self.origin,timezone.utc).isoformat()
        (self.root/'hls/stream.m3u8').write_text('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:'+date+'\n#EXTINF:4,\nseg_100.ts\n')
        self.timing = dict(started_at=self.started,origin=self.origin,tracks=[
            dict(id=1,type='track',duration=.8,offset=0,next_track_id=2),
            dict(id=None,type='jingle',duration=.8,offset=.8,next_track_id=2),
            dict(id=2,type='track',duration=2,offset=1.6,next_track_id=3),
            dict(id=3,type='track',duration=2,offset=4,next_track_id=4)])
        self.save()

    def save(self):
        (self.root/'hls/timeline.json').write_text(json.dumps(self.timing))

    def test_audible_metadata_and_history_never_run_ahead_of_listener(self):
        data = self.get('/preview-radio/now-playing?at='+str(self.origin+.4)).json
        self.assertEqual(data['track_id'],1)
        self.assertEqual(data['history'],[])
        self.assertEqual(data['next_track_id'],2)
        self.assertTrue(data['timing_available'])
        data = self.get('/preview-radio/now-playing?at='+str(self.origin+1.2)).json
        self.assertEqual(data['type'],'jingle')
        self.assertIsNone(data['artist'])
        self.assertIsNone(data['cover_url'])
        self.assertEqual([x['track_id'] for x in data['history']],[1])
        data = self.get('/preview-radio/now-playing?at='+str(self.origin+1.8)).json
        self.assertEqual(data['track_id'],2)
        self.assertEqual([x['type'] for x in data['history']],['jingle','track'])
        self.assertTrue(all(x['ended_at']<=self.origin+1.8 for x in data['history']))

    def test_without_listener_clock_published_edge_not_encoder_future(self):
        data = self.get('/preview-radio/now-playing').json
        self.assertEqual(data['track_id'],2)
        self.assertEqual(data['metadata_basis'],'published-live-edge')
        self.assertFalse(data['timing_available'])
        self.assertNotIn(3,[x['track_id'] for x in data['history']])

    def test_native_buffer_older_than_sliding_playlist_still_resolves(self):
        # The listener still has PCM from the first track in its native buffer,
        # but the server playlist has advanced beyond it. No audio restart needed.
        date = datetime.fromtimestamp(self.origin+40,timezone.utc).isoformat()
        (self.root/'hls/stream.m3u8').write_text('#EXTM3U\n#EXT-X-PROGRAM-DATE-TIME:'+date+'\n#EXTINF:4,\nseg_100.ts\n')
        for offset,kind,tid in ((.4,'track',1),(1.2,'jingle',None),(1.8,'track',2)):
            data = self.get('/preview-radio/now-playing?at='+str(self.origin+offset)).json
            self.assertEqual(data['type'],kind)
            self.assertEqual(data['track_id'],tid)
            self.assertEqual(data['metadata_basis'],'audible-clock')
            self.assertEqual(data['metadata_status'],'synchronized')
            self.assertEqual(data['next_track_id'],2 if offset<1.6 else 3)
            self.assertLess(data['metadata_at'],data['published_start'])
            self.assertGreater(data['playback_ended_at'],data['metadata_at'])

    def test_clock_outside_retained_session_never_shows_a_guessed_title(self):
        self.timing['tracks']=self.timing['tracks'][2:]
        self.save()
        data = self.get('/preview-radio/now-playing?at='+str(self.origin+.4)).json
        self.assertIsNone(data['track_id'])
        self.assertEqual(data['metadata_status'],'clock-outside-emitted-timeline')

    def test_confirmed_reservation_survives_invalid_native_clock(self):
        for at in ('nan',str(self.origin-30),str(self.origin+30)):
            data = self.get('/preview-radio/now-playing?at='+at).json
            self.assertIsNone(data['track_id'])
            self.assertEqual(data['next_track_id'],3)
            self.assertTrue(data['next_title'])
            self.assertTrue(data['next_cover_url'])
            self.assertEqual(data['next_metadata_basis'],'published-live-edge')
            self.assertFalse(data['timing_available'])

    def test_old_timeline_invalid_clock_and_stopped_stream_fail_closed(self):
        for at in ('nan','inf',str(self.origin+30),str(self.origin-30)):
            data = self.get('/preview-radio/now-playing?at='+at).json
            self.assertIsNone(data['track_id'])
            self.assertEqual(data['history'],[])
        self.timing['started_at']=self.started-10
        self.save()
        data = self.get('/preview-radio/now-playing').json
        self.assertIsNone(data['track_id'])
        self.assertEqual(data['history'],[])
        self.controller.state='inactive'
        self.assertFalse(self.get('/preview-radio/now-playing').json['available'])

    def test_private_access_preserved(self):
        with self.client.session_transaction(base_url=self.base) as session:
            session.clear()
        for route in ('/preview-radio/now-playing','/preview-radio/hls/stream.m3u8','/preview-radio/hls/seg_100.ts'):
            self.assertEqual(self.get(route).status_code,401)
