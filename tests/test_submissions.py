import copy
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import db
from metadata import MetadataURLValidationError
from submissions import SubmissionError, SubmissionService


VIDEO = 'dQw4w9WgXcQ'
URL = f'https://youtu.be/{VIDEO}'
SPOTIFY_ID = '4uLU6hMCjMI75M1A2tKUQC'
SPOTIFY_URL = f'https://open.spotify.com/track/{SPOTIFY_ID}'
YOUTUBE = {'items': [{'id': VIDEO, 'snippet': {'title': 'Provider title', 'channelTitle': 'Channel',
    'thumbnails': {'high': {'url': 'https://i.ytimg.com/fake.jpg'}}},
    'contentDetails': {'duration': 'PT2M'}, 'statistics': {'viewCount': '12'}}]}


def response(payload, status=200):
    result = Mock(status_code=status, headers={})
    result.json.return_value = payload
    return result


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'submissions.sqlite'
        db.init_db(self.path)
        self.conn = self.connect()
        for uid, role, verified in ((1,'contributor',1),(2,'contributor',1),(3,'admin',1),(4,'contributor',0)):
            self.conn.execute('INSERT INTO users(id,email,role,is_verified) VALUES (?,?,?,?)',
                              (uid,f'user{uid}@example.test',role,verified))
        self.conn.commit()
        self.service = SubmissionService(self.conn)
        environment = patch.dict(os.environ, {'YOUTUBE_API_KEY':'fake-unit-test-key',
            'SPOTIFY_METADATA_MODE':'disabled', 'SPOTIFY_CLIENT_ID':'fake-client',
            'SPOTIFY_CLIENT_SECRET':'fake-secret', 'SPOTIFY_MARKET':'FR'})
        environment.start()
        self.addCleanup(environment.stop)
        socket = patch('socket.socket', side_effect=AssertionError('No real network'))
        socket.start()
        self.addCleanup(socket.stop)
        get = patch('requests.get', return_value=response(copy.deepcopy(YOUTUBE)))
        post = patch('requests.post', return_value=response({'access_token':'fake-token','token_type':'Bearer','expires_in':3600}))
        self.get, self.post = get.start(), post.start()
        self.addCleanup(get.stop)
        self.addCleanup(post.stop)

    def connect(self):
        conn = db.get_db(self.path)
        self.addCleanup(conn.close)
        return conn

    def draft(self, actor=1, url=URL):
        return self.service.create_draft(actor,url,fetch_metadata=False)['id']

    def editorial(self, sid):
        return self.service.update_editorial(1,sid,title=' Manual title ',artist=' Manual artist ',genre_id=1)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(SubmissionError) as caught:
            function(*args,**kwargs)
        self.assertEqual(caught.exception.code,code)

    def assert_no_catalog(self):
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM plays').fetchone()[0],0)

    def assert_outside_transaction(self):
        self.assertFalse(self.conn.in_transaction)
        other = self.connect()
        # A provider call must not hold a SQLite write lock on another connection.
        other.execute('BEGIN IMMEDIATE')
        other.rollback()

    def test_url_first_creation_and_youtube_orchestration(self):
        row = self.service.create_draft(1,URL)
        self.assertEqual(row['contributor_id'],1)
        self.assertEqual(row['source_id'],VIDEO)
        self.assertEqual(row['canonical_url'],f'https://www.youtube.com/watch?v={VIDEO}')
        self.assertEqual(row['status'],'draft')
        self.assertEqual(row['metadata_status'],'available')
        self.assertEqual(row['editorial_suggestions'],{'title':'Provider title','artist':None})
        self.assertIsNone(row['artist'])  # Channel must never substitute for artist.
        self.assertIsNone(row['title'])
        self.assertIsNone(row['track_id'])
        self.get.assert_called_once()
        self.assert_no_catalog()

    def test_deferred_metadata_creates_no_attempt_or_request(self):
        sid = self.draft()
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.assertEqual(self.service.get_history(1,sid),[])

    def test_same_contributor_duplicate_variants_blocked(self):
        self.draft()
        self.assert_code('duplicate_source',self.service.create_draft,1,
            f'https://www.youtube.com/watch?v={VIDEO}&t=12',fetch_metadata=False)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],1)
        self.get.assert_not_called()

    def test_other_contributor_same_source_permitted_with_warning(self):
        sid = self.draft()
        other = self.service.create_draft(2,URL,fetch_metadata=False)
        self.assertTrue(other['has_other_contributor_source'])
        self.assertTrue(self.service.get_submission(1,sid)['has_other_contributor_source'])
        self.assertNotIn('other_contributor_id',other)

    def test_rejected_attempt_allows_new_draft_with_history(self):
        sid = self.draft()
        self.editorial(sid)
        self.service.submit_for_review(1,sid)
        self.conn.execute("""UPDATE submissions SET status='rejected',reviewed_by=3,
            reviewed_at=CURRENT_TIMESTAMP,rejection_reason='Not suitable' WHERE id=?""",(sid,))
        self.conn.commit()
        new = self.service.create_draft(1,URL,fetch_metadata=False)
        self.assertNotEqual(new['id'],sid)
        self.assertEqual(self.service.get_submission(1,sid)['status'],'rejected')

    def test_concurrent_duplicate_creation(self):
        def create():
            conn = db.get_db(self.path)
            try:
                try:
                    return SubmissionService(conn).create_draft(1,URL,fetch_metadata=False)['id']
                except SubmissionError as error:
                    return error.code
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:create(),range(2)))
        self.assertEqual(sum(type(r) is int for r in results),1)
        self.assertIn('duplicate_source',results)

    def test_unknown_unverified_and_invalid_actors_rejected(self):
        for actor in (4,999):
            self.assert_code('unauthorized',self.service.create_draft,actor,URL)
        for actor in (None,True,'1',0,2**63):
            self.assert_code('invalid_identifier',self.service.create_draft,actor,URL)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)
        self.get.assert_not_called()

    def test_admin_may_only_access_own_contribution(self):
        self.draft()
        other = self.service.create_draft(3,'https://youtu.be/abcdefghijk',fetch_metadata=False)
        self.assertEqual(other['contributor_id'],3)
        self.assert_code('not_found',self.service.get_submission,1,other['id'])

    def test_all_operations_enforce_ownership(self):
        sid = self.draft()
        for actor in (2,3):
            for operation, kwargs in ((self.service.get_submission,{}),
                    (self.service.refresh_metadata,{}), (self.service.update_editorial,{'title':'stolen'}),
                    (self.service.submit_for_review,{}), (self.service.get_history,{})):
                with self.subTest(actor=actor,operation=operation.__name__):
                    self.assert_code('not_found',operation,actor,sid,**kwargs)
        self.get.assert_not_called()
        self.assertEqual(self.service.get_history(1,sid),[])

    def test_authentication_context_rechecked_on_each_operation(self):
        sid = self.draft()
        self.conn.execute('UPDATE users SET is_verified=0 WHERE id=1')
        self.conn.commit()
        self.assert_code('unauthorized',self.service.update_editorial,1,sid,title='no')
        self.assert_code('unauthorized',self.service.refresh_metadata,1,sid)
        self.get.assert_not_called()

    def test_editorial_patch_and_manual_clear(self):
        sid = self.draft()
        row = self.editorial(sid)
        self.assertEqual((row['title'],row['artist'],row['genre_id']),('Manual title','Manual artist',1))
        row = self.service.update_editorial(1,sid,title='Changed')
        self.assertEqual(row['artist'],'Manual artist')
        row = self.service.update_editorial(1,sid,artist=' ',genre_id=None)
        self.assertIsNone(row['artist'])
        self.assertIsNone(row['genre_id'])
        self.assert_code('incomplete_editorial',self.service.submit_for_review,1,sid)

    def test_invalid_genre_rolls_back_all_editorial_changes(self):
        sid = self.draft()
        self.editorial(sid)
        for genre in (999,0,-1,True,'1',1.0,2**63):
            self.assert_code('invalid_genre',self.service.update_editorial,1,sid,title='Should roll back',genre_id=genre)
        self.assertEqual(self.service.get_submission(1,sid)['title'],'Manual title')

    def test_invalid_editorial_types_and_controls(self):
        sid = self.draft()
        for value in (123,[], 'bad\x00value','bad\nvalue'):
            self.assert_code('invalid_editorial',self.service.update_editorial,1,sid,title=value)

    def test_submit_requires_all_editorial_fields(self):
        sid = self.draft()
        self.assert_code('incomplete_editorial',self.service.submit_for_review,1,sid)
        self.service.update_editorial(1,sid,title='Title')
        self.assert_code('incomplete_editorial',self.service.submit_for_review,1,sid)
        self.service.update_editorial(1,sid,artist='Artist')
        self.assert_code('incomplete_editorial',self.service.submit_for_review,1,sid)
        self.service.update_editorial(1,sid,genre_id=1)
        row = self.service.submit_for_review(1,sid)
        self.assertEqual(row['status'],'pending_review')
        self.assertIsNone(row['reviewed_by'])
        self.assertIsNone(row['reviewed_at'])
        self.assert_no_catalog()

    def test_metadata_does_not_implicitly_submit(self):
        row = self.service.create_draft(1,URL)
        self.assert_code('incomplete_editorial',self.service.submit_for_review,1,row['id'])
        self.assertEqual(self.service.get_submission(1,row['id'])['status'],'draft')

    def test_pending_review_blocks_edit_and_repeat_submit_but_allows_refresh(self):
        sid = self.draft()
        self.editorial(sid)
        self.service.submit_for_review(1,sid)
        self.assert_code('invalid_state',self.service.update_editorial,1,sid,title='Changed')
        self.assert_code('invalid_state',self.service.submit_for_review,1,sid)
        row = self.service.refresh_metadata(1,sid)
        self.assertEqual(row['status'],'pending_review')
        self.assertEqual(row['title'],'Manual title')

    def test_post_review_states_cannot_be_changed_by_contributor(self):
        sid = self.draft()
        self.editorial(sid)
        self.service.submit_for_review(1,sid)
        self.conn.execute("""UPDATE submissions SET status='approved_awaiting_audio',
            reviewed_by=3,reviewed_at=CURRENT_TIMESTAMP WHERE id=?""",(sid,))
        self.conn.commit()
        for operation in (self.service.update_editorial,self.service.submit_for_review,self.service.refresh_metadata):
            self.assert_code('invalid_state',operation,1,sid)
        self.get.assert_not_called()
        self.assert_no_catalog()

    def test_rejected_state_cannot_be_reopened(self):
        sid = self.draft()
        self.editorial(sid)
        self.service.submit_for_review(1,sid)
        self.conn.execute("""UPDATE submissions SET status='rejected',reviewed_by=3,
            reviewed_at=CURRENT_TIMESTAMP,rejection_reason='No' WHERE id=?""",(sid,))
        self.conn.commit()
        for operation in (self.service.refresh_metadata,self.service.submit_for_review,self.service.update_editorial):
            self.assert_code('invalid_state',operation,1,sid)

    def test_refresh_preserves_manual_corrections_and_intentional_empty(self):
        sid = self.draft()
        self.editorial(sid)
        row = self.service.refresh_metadata(1,sid)
        self.assertEqual((row['title'],row['artist'],row['genre_id']),('Manual title','Manual artist',1))
        self.service.update_editorial(1,sid,title=None)
        self.assertIsNone(self.service.refresh_metadata(1,sid)['title'])

    def test_http_executes_outside_transaction_and_concurrent_edit_survives(self):
        sid = self.draft()
        def youtube(*args,**kwargs):
            self.assert_outside_transaction()
            self.service.update_editorial(1,sid,title='Edited during request',artist='Artist',genre_id=1)
            return response(copy.deepcopy(YOUTUBE))
        self.get.side_effect = youtube
        row = self.service.refresh_metadata(1,sid)
        self.assertEqual(row['title'],'Edited during request')
        self.assertEqual(row['source_title'],'Provider title')

    def test_spotify_disabled_allows_manual_submission_without_adapter_call(self):
        with patch('submissions.SpotifyMetadataClient.fetch',side_effect=AssertionError('Must not call adapter')):
            row = self.service.create_draft(1,SPOTIFY_URL)
        self.assertEqual(row['metadata_error_code'],'disabled')
        self.assertEqual(row['status'],'draft')
        self.editorial(row['id'])
        self.assertEqual(self.service.submit_for_review(1,row['id'])['status'],'pending_review')
        self.get.assert_not_called()
        self.post.assert_not_called()
        self.assert_no_catalog()

    def test_spotify_invalid_mode_no_network(self):
        with patch.dict(os.environ,{'SPOTIFY_METADATA_MODE':'oembed'}):
            row = self.service.create_draft(1,SPOTIFY_URL)
        self.assertEqual(row['metadata_error_code'],'invalid_configuration')
        self.get.assert_not_called()
        self.post.assert_not_called()

    def test_spotify_explicit_mode_uses_mocked_official_api_outside_transactions(self):
        def token(*args,**kwargs):
            self.assert_outside_transaction()
            return response({'access_token':'fake-token','token_type':'Bearer','expires_in':3600})
        def track(*args,**kwargs):
            self.assert_outside_transaction()
            return response({'id':SPOTIFY_ID,'name':'Spotify title','artists':[{'name':'Artist A'}],
                'duration_ms':1000,'album':{'images':[{'url':'https://i.scdn.co/fake'}]}})
        self.post.side_effect, self.get.side_effect = token, track
        with patch.dict(os.environ,{'SPOTIFY_METADATA_MODE':'web_api'}):
            row = self.service.create_draft(1,SPOTIFY_URL)
        self.assertEqual(row['artist_name'],'Artist A')
        self.assertEqual(row['editorial_suggestions']['artist'],'Artist A')
        self.assertEqual(row['metadata_status'],'available')
        self.assertEqual(row['status'],'draft')
        self.assertIsNone(row['artwork_path'])
        self.assert_no_catalog()

    def test_incomplete_metadata_still_allows_manual_review_submission(self):
        self.get.return_value = response({'items':[{'id':VIDEO}]})
        row = self.service.create_draft(1,URL)
        self.assertEqual(row['metadata_status'],'partial')
        self.editorial(row['id'])
        self.assertEqual(self.service.submit_for_review(1,row['id'])['status'],'pending_review')

    def test_network_failure_then_explicit_retry(self):
        self.get.side_effect = requests.Timeout('fake-sensitive-detail')
        row = self.service.create_draft(1,URL)
        self.assertEqual(row['metadata_error_code'],'timeout')
        self.assertEqual(row['status'],'draft')
        self.editorial(row['id'])
        self.get.side_effect = None
        retried = self.service.refresh_metadata(1,row['id'])
        self.assertEqual(retried['metadata_status'],'available')
        self.assertIsNone(retried['metadata_error_code'])
        self.assertEqual(retried['metadata_generation'],2)
        self.assertEqual([r['status'] for r in self.service.get_history(1,row['id'])],['available','failed'])
        self.assertEqual(retried['title'],'Manual title')

    def test_missing_api_key_draft_survives(self):
        with patch.dict(os.environ,{'YOUTUBE_API_KEY':''}):
            row = self.service.create_draft(1,URL)
        self.assertEqual(row['metadata_error_code'],'missing_api_key')
        self.get.assert_not_called()

    def test_quota_failure_no_retry_or_fallback(self):
        self.get.return_value = response({'error':{'errors':[{'reason':'quotaExceeded'}]}},403)
        row = self.service.create_draft(1,URL)
        self.assertEqual(row['metadata_error_code'],'quota_exceeded')
        self.get.assert_called_once()
        self.post.assert_not_called()

    def test_invalid_url_leaves_no_submission(self):
        for value in ('https://evil.test/watch?v='+VIDEO,'javascript:bad','https://youtube.com/playlist?list=x'):
            with self.assertRaises(MetadataURLValidationError):
                self.service.create_draft(1,value)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)
        self.get.assert_not_called()

    def test_active_transaction_refused_without_implicit_commit(self):
        self.conn.execute('BEGIN')
        self.assert_code('active_transaction',self.service.create_draft,1,URL)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.get.assert_not_called()

    def test_foreign_keys_required(self):
        self.conn.execute('PRAGMA foreign_keys=OFF')
        self.assert_code('foreign_keys_disabled',self.service.create_draft,1,URL)
        self.get.assert_not_called()

    def test_refresh_completion_after_rejection_is_superseded(self):
        sid = self.draft()
        self.editorial(sid)
        self.service.submit_for_review(1,sid)
        def reject_during_call(*args,**kwargs):
            self.assert_outside_transaction()
            self.conn.execute("""UPDATE submissions SET status='rejected',reviewed_by=3,
                reviewed_at=CURRENT_TIMESTAMP,rejection_reason='No' WHERE id=?""",(sid,))
            self.conn.commit()
            return response(copy.deepcopy(YOUTUBE))
        self.get.side_effect = reject_during_call
        row = self.service.refresh_metadata(1,sid)
        self.assertEqual(row['status'],'rejected')
        self.assertIsNone(row['source_title'])
        self.assertEqual(self.service.get_history(1,sid)[0]['status'],'superseded')

    def test_missing_submission_error_does_not_disclose_other_owner(self):
        self.assert_code('not_found',self.service.get_submission,1,999)
        self.assert_code('not_found',self.service.get_history,1,999)

    def test_existing_approved_legacy_track_is_untouched(self):
        tid = self.conn.execute("""INSERT INTO tracks(uploader_id,file_path,title,genre_id,status)
            VALUES (1,'tracks/legacy.mp3','Legacy',1,'approved')""").lastrowid
        self.conn.commit()
        before = tuple(self.conn.execute('SELECT * FROM tracks WHERE id=?',(tid,)).fetchone())
        row = self.service.create_draft(1,URL)
        self.editorial(row['id'])
        self.service.submit_for_review(1,row['id'])
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM tracks WHERE id=?',(tid,)).fetchone()),before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tracks WHERE status='approved'").fetchone()[0],1)

    def test_retry_started_during_provider_call_wins(self):
        sid = self.draft()
        def nested_retry(*args,**kwargs):
            self.assert_outside_transaction()
            self.get.side_effect = None
            newest = copy.deepcopy(YOUTUBE)
            newest['items'][0]['snippet']['title']='Newest response'
            self.get.return_value = response(newest)
            self.service.refresh_metadata(1,sid)
            return response(copy.deepcopy(YOUTUBE))
        self.get.side_effect = nested_retry
        row = self.service.refresh_metadata(1,sid)
        self.assertEqual(row['source_title'],'Newest response')
        self.assertEqual(row['metadata_generation'],2)
        self.assertEqual([r['status'] for r in self.service.get_history(1,sid)],['available','superseded'])

    def test_account_check_repeated_after_provider_call(self):
        sid = self.draft()
        def revoke(*args,**kwargs):
            self.conn.execute('UPDATE users SET is_verified=0 WHERE id=1')
            self.conn.commit()
            return response(copy.deepcopy(YOUTUBE))
        self.get.side_effect = revoke
        self.assert_code('unauthorized',self.service.refresh_metadata,1,sid)
        self.assertIsNone(self.conn.execute('SELECT source_title FROM submissions WHERE id=?',(sid,)).fetchone()[0])


if __name__=='__main__':
    unittest.main()
