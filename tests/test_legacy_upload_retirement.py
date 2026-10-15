"""Retired direct uploads and legacy compatibility; only temporary DB/media.

The worker is imported with an isolated config and only its catalogue selector is
called. No encoder, provider HTTP request or production configuration is used.
"""
import concurrent.futures
import importlib.util
import io
import json
import sqlite3
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.datastructures import FileStorage
from werkzeug.test import encode_multipart
from flask import Request

import db
from media_proof_fixtures import add_media_proof
import test_flask_submissions as contributor
from submission_review import SubmissionReviewService


class LegacyUploadRetirementTests(unittest.TestCase):
    setUpClass = classmethod(contributor.FlaskSubmissionTests.setUpClass.__func__)
    setUp = contributor.FlaskSubmissionTests.setUp
    get = contributor.FlaskSubmissionTests.get
    post = contributor.FlaskSubmissionTests.post
    token = contributor.FlaskSubmissionTests.token
    session_user = contributor.FlaskSubmissionTests.session_user
    mutate = contributor.FlaskSubmissionTests.mutate
    draft = contributor.FlaskSubmissionTests.draft
    complete = contributor.FlaskSubmissionTests.complete
    assert_no_track = contributor.FlaskSubmissionTests.assert_no_track
    track = contributor.security.FlaskSecurityTests.track

    def snapshot(self):
        tables = ('users','tracks','submissions','submission_metadata_attempts','plays')
        rows = {table:[tuple(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in tables}
        files = {str(path.relative_to(self.temp.name)):path.read_bytes()
                 for path in Path(self.temp.name).rglob('*') if path.is_file()}
        return rows, files

    def media_fixture(self, tid):
        media = Path(self.temp.name)/'tracks'/'test.mp3'
        media.parent.mkdir(exist_ok=True)
        media.write_bytes(b'isolated-existing-audio')
        cover = Path(self.temp.name)/f'{tid}.jpg'
        cover.write_bytes(b'isolated-existing-cover')
        self.conn.execute('UPDATE tracks SET meta_path=? WHERE id=?',(cover.name,tid))
        self.conn.execute('INSERT INTO plays(track_id,started_at) VALUES (?,CURRENT_TIMESTAMP)',(tid,))
        self.conn.commit()
        return media, cover

    def accepted(self):
        sid = self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        SubmissionReviewService(self.conn).accept(2,sid)
        return sid

    def linked(self):
        sid = self.accepted()
        tid = self.track(source_url=contributor.URL)
        add_media_proof(self.conn, sid, tid)
        self.conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(tid,sid))
        self.conn.commit()
        self.media_fixture(tid)
        return sid, tid

    def test_valid_csrf_upload_refused_without_sql_or_storage_writes(self):
        token = self.token()
        before = self.snapshot()
        statements = []
        def factory():
            conn = db.get_db(self.path)
            conn.set_trace_callback(statements.append)
            return conn
        with patch.object(self.module,'get_db',side_effect=factory), \
                patch.object(FileStorage,'save',side_effect=AssertionError('No file save')):
            result = self.post('/tracks',data={'csrf_token':token,'title':'Blocked',
                'genre_id':'1','source_url':contributor.URL,
                'file':(io.BytesIO(b'fake-mp3'),'test.mp3')},content_type='multipart/form-data')
        self.assertEqual(result.status_code,410)
        self.assertEqual(result.json['code'],'direct_upload_disabled')
        self.assertEqual(result.json['submissions_url'],'/submissions')
        self.assertIn('/submissions',result.json['error'])
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','BEGIN','REPLACE'))
                             for sql in statements))
        self.assertEqual(self.snapshot(),before)
        self.http.assert_not_called()

    def test_header_csrf_rejection_does_not_even_parse_uploaded_file(self):
        # Header clients need no multipart parsing or temporary upload spool.
        token = self.token()
        with io.BytesIO(b'x'*1_000_000) as stream:
            boundary, payload = encode_multipart({'file':FileStorage(stream=stream,filename='large.mp3')})
        with patch.object(Request,'_get_file_stream',side_effect=AssertionError('No upload spool')):
            result = self.post('/tracks',headers={'X-CSRF-Token':token},
                data=payload,content_type=f'multipart/form-data; boundary={boundary}')
        self.assertEqual(result.status_code,410)
        self.assert_no_track()

    def test_admin_cannot_use_retired_upload(self):
        self.session_user(2)
        self.assertEqual(self.mutate('/tracks',{'title':'Admin','genre_id':'1'}).status_code,410)
        self.assert_no_track()

    def test_accepted_submission_id_does_not_reopen_direct_upload(self):
        sid = self.accepted()
        before = self.snapshot()
        self.assertEqual(self.mutate('/tracks',{'submission_id':str(sid),'title':'Bypass','genre_id':'1',
            'file':(io.BytesIO(b'fake'),'test.mp3')},content_type='multipart/form-data').status_code,410)
        self.assertEqual(self.snapshot(),before)

    def test_json_empty_and_invalid_audio_payloads_cannot_create_tracks(self):
        for data in ({},{'title':'Missing audio','genre_id':'1'},
                     {'title':'Wrong extension','genre_id':'1','file':(io.BytesIO(b'fake'),'test.wav')}):
            self.assertEqual(self.mutate('/tracks',data).status_code,410)
        self.assertEqual(self.post('/tracks',json={'file_path':'/private/path','status':'approved'},
            headers={'X-CSRF-Token':self.token()}).status_code,410)
        self.assert_no_track()

    def test_authentication_and_csrf_are_still_enforced(self):
        for uid in (1,2):
            self.session_user(uid)
            self.assertEqual(self.post('/tracks').status_code,400)
            self.assertEqual(self.post('/tracks',data={'csrf_token':'wrong'}).status_code,400)
        with self.client.session_transaction(base_url=self.base) as session: session.clear()
        self.assertEqual(self.post('/tracks',headers={'X-CSRF-Token':self.token()}).status_code,401)
        self.assert_no_track()

    def test_home_has_clear_url_entry_and_radio_without_mp3_form(self):
        text = self.get('/').get_data(as_text=True)
        self.assertIn('href="/submissions"',text)
        self.assertIn('YouTube ou Spotify',text)
        self.assertIn('validation audio distincte',text)
        self.assertIn('id="player"',text)
        self.assertIn('/hls/stream.m3u8',text)
        for old in ('action="/tracks"','multipart/form-data'):
            self.assertNotIn(old,text)
        self.assertNotRegex(text,r'<input\b[^>]*type=[\"\x27]file[\"\x27]')

    def test_legacy_audio_cover_catalogue_and_play_history_preserved(self):
        tid = self.track('approved')
        media, cover = self.media_fixture(tid)
        self.session_user(2)
        before = self.snapshot()
        audio_response = self.get(f'/media/{tid}')
        self.assertEqual(audio_response.status_code,200)
        self.assertEqual(audio_response.data,media.read_bytes())
        audio_response.close()
        cover_response = self.get(f'/media/cover/{tid}')
        self.assertEqual(cover_response.status_code,200)
        self.assertEqual(cover_response.data,cover.read_bytes())
        cover_response.close()
        self.assertIn('Legacy',self.get('/admin').get_data(as_text=True))
        self.assertEqual(self.mutate('/tracks').status_code,410)
        self.assertEqual(self.snapshot(),before)

    def test_cover_missing_file_and_unknown_track_keep_fallback(self):
        tid = self.track('approved')
        self.conn.execute("UPDATE tracks SET meta_path='missing.jpg' WHERE id=?",(tid,))
        self.conn.commit()
        for track_id in (tid,999):
            self.assertEqual(self.get(f'/media/cover/{track_id}').location,'/static/radio-logo.png')

    def test_legacy_moderation_retains_files_and_history(self):
        tid = self.track()
        media, cover = self.media_fixture(tid)
        self.session_user(2)
        play_before = [tuple(row) for row in self.conn.execute('SELECT * FROM plays')]
        for action, status in (('approve','approved'),('reject','rejected'),('reintegrate','approved')):
            result = self.mutate(f'/admin/tracks/{tid}/{action}',{'reason':'Legacy reason'})
            self.assertEqual(result.status_code,302)
            self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(tid,)).fetchone()[0],status)
            self.assertTrue(media.is_file())
            self.assertTrue(cover.is_file())
            self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM plays')],play_before)

    def test_explicit_legacy_delete_still_removes_only_temporary_fixture(self):
        tid = self.track('rejected')
        media, cover = self.media_fixture(tid)
        self.session_user(2)
        self.assertEqual(self.mutate(f'/admin/tracks/{tid}/delete').status_code,302)
        self.assertFalse(media.exists())
        self.assertFalse(cover.exists())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM plays').fetchone()[0],0)

    def test_linked_pending_audio_cannot_use_any_legacy_action(self):
        sid, tid = self.linked()
        self.assert_linked_protected(sid,tid)

    def test_linked_approved_audio_cannot_be_rejected_or_rewritten_by_legacy_actions(self):
        sid, tid = self.linked()
        db.finalize_audio_review(self.conn,sid,2,approve=True)
        self.assert_linked_protected(sid,tid)

    def test_linked_rejected_audio_history_cannot_be_reintegrated_or_deleted(self):
        sid, tid = self.linked()
        db.finalize_audio_review(self.conn,sid,2,approve=False,reason='Wrong audio')
        self.assert_linked_protected(sid,tid)

    def assert_linked_protected(self,sid,tid):
        self.session_user(2)
        before = self.snapshot()
        with patch.object(self.module,'download_og_image',create=True) as download:
            for action in ('approve','reject','reintegrate','delete','refetch-cover'):
                with self.subTest(action=action):
                    result = self.mutate(f'/admin/tracks/{tid}/{action}',{'reason':'Bypass'})
                    self.assertEqual(result.status_code,409)
                    self.assertEqual(result.json['code'],'linked_submission_track')
                    self.assertEqual(result.json['submission_url'],f'/admin/submissions/{sid}')
                    self.assertEqual(self.snapshot(),before)
            download.assert_not_called()
        text = self.get('/admin').get_data(as_text=True)
        self.assertIn(f'/admin/submissions/{sid}',text)
        self.assertIn(f'src="/media/{tid}"',text)
        for action in ('approve','reject','reintegrate','delete','refetch-cover'):
            self.assertNotIn(f'action="/admin/tracks/{tid}/{action}"',text)

    def test_legacy_actions_missing_record_and_connections_closed(self):
        self.session_user(2)
        opened = []
        def factory():
            conn = db.get_db(self.path)
            opened.append(conn)
            return conn
        with patch.object(self.module,'get_db',side_effect=factory):
            for action in ('approve','reject','reintegrate','delete','refetch-cover'):
                self.assertEqual(self.mutate(f'/admin/tracks/999/{action}').status_code,302)
        for conn in opened:
            with self.assertRaises(sqlite3.ProgrammingError): conn.execute('SELECT 1')

    def test_linked_check_waits_for_concurrent_attachment_before_deciding(self):
        sid = self.accepted()
        tid = self.track()
        self.session_user(2)
        token = self.token()
        writer = db.get_db(self.path)
        self.addCleanup(writer.close)
        writer.execute('BEGIN IMMEDIATE')
        add_media_proof(writer,sid,tid)
        writer.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(tid,sid))
        waiting = threading.Event()
        def factory():
            conn = db.get_db(self.path)
            conn.set_trace_callback(lambda sql: waiting.set() if sql == 'BEGIN IMMEDIATE' else None)
            return conn
        with patch.object(self.module,'get_db',side_effect=factory):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(self.post,f'/admin/tracks/{tid}/approve',data={'csrf_token':token})
                try:
                    self.assertTrue(waiting.wait(timeout=5))
                finally:
                    writer.commit()  # Release lock even if the test assertion fails.
                response = result.result(timeout=10)
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(tid,)).fetchone()[0],'pending')
        self.assertEqual(self.service.get_submission(1,sid)['status'],'pending_audio_review')

    def test_transaction_failure_does_not_partially_moderate_legacy_track(self):
        tid = self.track()
        self.conn.execute("""CREATE TRIGGER fail_legacy AFTER UPDATE OF status ON tracks
            BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
        self.session_user(2)
        with self.assertRaises(sqlite3.IntegrityError): self.mutate(f'/admin/tracks/{tid}/approve')
        row = self.conn.execute('SELECT * FROM tracks WHERE id=?',(tid,)).fetchone()
        self.assertEqual(row['status'],'pending')
        self.assertIsNone(row['reviewed_by'])
        self.conn.execute('DROP TRIGGER fail_legacy')
        self.assertEqual(self.mutate(f'/admin/tracks/{tid}/approve').status_code,302)

    def test_worker_still_selects_only_existing_approved_legacy_tracks(self):
        approved = self.track('approved')
        self.media_fixture(approved)
        self.track('pending')
        self.track('rejected')
        config = types.ModuleType('config')
        config.STORAGE_PATH = self.temp.name
        config.HLS_PATH = self.temp.name
        spec = importlib.util.spec_from_file_location('isolated_legacy_worker',Path(__file__).resolve().parents[1]/'worker.py')
        worker = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'config':config}): spec.loader.exec_module(worker)
        with patch.object(worker,'get_db',side_effect=lambda:db.get_db(self.path)), \
                patch.object(worker.subprocess,'Popen',side_effect=AssertionError('No FFmpeg')):
            for last_genre in (None,'jingle','deep_house'):
                self.assertEqual(worker.get_next_track(last_genre)['id'],approved)
            self.session_user(2)
            self.assertEqual(self.mutate(f'/admin/tracks/{approved}/reject').status_code,302)
            self.assertIsNone(worker.get_next_track(None))

    def test_url_workflow_unchanged_and_no_automatic_migration(self):
        with patch.object(db,'migrate_db',side_effect=AssertionError('No migration')), \
                patch.object(db,'init_db',side_effect=AssertionError('No initialization')):
            created = self.mutate('/submissions',{'source_url':contributor.URL})
            self.assertEqual(created.status_code,303)
            sid = self.conn.execute('SELECT id FROM submissions').fetchone()[0]
            self.assertEqual(self.mutate(f'/submissions/{sid}/editorial',
                {'title':'My track','artist':'My artist','genre_id':'1'}).status_code,303)
            self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,303)
            self.session_user(2)
            self.assertEqual(self.mutate(f'/admin/submissions/{sid}/accept').status_code,303)
            self.assertEqual(self.mutate('/tracks',{'submission_id':str(sid)}).status_code,410)
            self.assertEqual(self.service.get_submission(1,sid)['status'],'approved_awaiting_audio')
            self.assert_no_track()

    def test_now_playing_for_historical_track_stays_available(self):
        tid = self.track('approved')
        (Path(self.temp.name)/'now.json').write_text(json.dumps({'track_id':tid,'title':'Legacy','started_at':0}))
        result = self.get('/now-playing')
        self.assertEqual(result.status_code,200)
        self.assertEqual((result.json['track_id'],result.json['title']),(tid,'Legacy'))
