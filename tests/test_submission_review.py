"""Pure business review tests: only isolated temporary SQLite databases."""
import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import db
from submissions import SubmissionService, SubmissionError
from submission_review import SubmissionReviewService

URL = 'https://youtu.be/dQw4w9WgXcQ'


class ReviewServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'review.sqlite'
        db.init_db(self.path)
        self.conn = db.get_db(self.path)
        self.addCleanup(self.conn.close)
        self.conn.executemany('INSERT INTO users(id,email,role,is_verified) VALUES (?,?,?,?)',
            [(1,'a@example.test','contributor',1), (2,'b@example.test','admin',1),
             (3,'c@example.test','admin',0), (4,'d@example.test','contributor',1)])
        self.conn.commit()
        self.service = SubmissionReviewService(self.conn)

    def pending(self, owner=1, url=URL):
        service = SubmissionService(self.conn)
        sid = service.create_draft(owner,url,fetch_metadata=False)['id']
        service.update_editorial(owner,sid,title='Title',artist='Artist',genre_id=1)
        service.submit_for_review(owner,sid)
        return sid

    def row(self,sid):
        return self.conn.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone()

    def test_accept_records_actual_admin_and_no_audio(self):
        sid = self.pending()
        self.service.accept(2,sid)
        row = self.row(sid)
        self.assertEqual((row['status'],row['reviewed_by']),('approved_awaiting_audio',2))
        self.assertTrue(row['reviewed_at'])
        self.assertIsNone(row['track_id'])
        self.assertIsNone(row['audio_reviewed_at'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)

    def test_reject_keeps_history_and_allows_new_attempt(self):
        sid = self.pending()
        self.service.reject(2,sid,reason=' Wrong source ')
        self.assertEqual(self.row(sid)['rejection_reason'],'Wrong source')
        new = SubmissionService(self.conn).create_draft(1,URL,fetch_metadata=False)
        self.assertNotEqual(new['id'],sid)
        self.assertEqual(self.service.get_submission(2,sid)['status'],'rejected')

    def test_current_verified_admin_required_for_every_operation(self):
        sid = self.pending()
        for uid in (1,3,99):
            for operation in (lambda:self.service.list_submissions(uid),
                              lambda:self.service.get_submission(uid,sid),
                              lambda:self.service.update_editorial(uid,sid,title='Bad'),
                              lambda:self.service.accept(uid,sid),
                              lambda:self.service.reject(uid,sid,reason='Bad')):
                with self.subTest(uid=uid), self.assertRaises(SubmissionError) as error:
                    operation()
                self.assertEqual(error.exception.code,'unauthorized')
        self.assertEqual(self.row(sid)['status'],'pending_review')

    def test_demoted_admin_checked_again(self):
        sid = self.pending()
        self.service.get_submission(2,sid)
        self.conn.execute("UPDATE users SET role='contributor' WHERE id=2")
        self.conn.commit()
        with self.assertRaises(SubmissionError):
            self.service.accept(2,sid)

    def test_editorial_patch_preserves_provider_and_review_fields(self):
        sid = self.pending()
        self.conn.execute("UPDATE submissions SET source_title='Provider' WHERE id=?",(sid,))
        self.conn.commit()
        self.service.update_editorial(2,sid,title=' Corrected ',genre_id=2)
        row = self.row(sid)
        self.assertEqual((row['title'],row['artist'],row['genre_id']),('Corrected','Artist',2))
        self.assertEqual(row['source_title'],'Provider')
        self.assertIsNone(row['reviewed_by'])
        self.assertEqual(row['status'],'pending_review')

    def test_invalid_editorial_is_atomic(self):
        sid = self.pending()
        for fields in ({'title':''},{'artist':None},{'genre_id':None}, {'title':'\nBad'},
                       {'title':'Changed','genre_id':999},{'genre_id':True}):
            with self.subTest(fields=fields), self.assertRaises(SubmissionError):
                self.service.update_editorial(2,sid,**fields)
            self.assertEqual(self.row(sid)['title'],'Title')
            self.assertFalse(self.conn.in_transaction)

    def test_rejection_requires_valid_reason(self):
        sid = self.pending()
        for reason in (None,'','   ','bad\nreason',123):
            with self.subTest(reason=reason), self.assertRaises(SubmissionError):
                self.service.reject(2,sid,reason=reason)
            self.assertIsNone(self.row(sid)['reviewed_at'])

    def test_draft_and_decided_states_reject_all_mutations(self):
        sid = SubmissionService(self.conn).create_draft(1,URL,fetch_metadata=False)['id']
        for operation in (lambda:self.service.accept(2,sid),
                          lambda:self.service.reject(2,sid,reason='No'),
                          lambda:self.service.update_editorial(2,sid,title='Changed')):
            with self.assertRaises(SubmissionError): operation()
        SubmissionService(self.conn).update_editorial(1,sid,title='Title',artist='Artist',genre_id=1)
        SubmissionService(self.conn).submit_for_review(1,sid)
        self.service.accept(2,sid)
        before = dict(self.row(sid))
        for operation in (lambda:self.service.accept(2,sid),
                          lambda:self.service.reject(2,sid,reason='No'),
                          lambda:self.service.update_editorial(2,sid,title='Changed')):
            with self.assertRaises(SubmissionError): operation()
        self.assertEqual(dict(self.row(sid)),before)

    def test_rejected_state_is_terminal(self):
        sid = self.pending()
        self.service.reject(2,sid,reason='No')
        with self.assertRaises(SubmissionError): self.service.accept(2,sid)
        with self.assertRaises(SubmissionError): self.service.reject(2,sid,reason='Other')
        self.assertEqual(self.row(sid)['rejection_reason'],'No')

    def test_duplicate_warning_does_not_block_decision(self):
        first, second = self.pending(), self.pending(owner=4)
        result = self.service.list_submissions(2)
        self.assertTrue(all(row['has_duplicate'] for row in result['items']))
        detail = self.service.get_submission(2,first)
        self.assertEqual(detail['duplicates'][0]['id'],second)
        self.service.reject(2,second,reason='No')
        self.assertEqual(self.service.get_submission(2,first)['duplicates'][0]['status'],'rejected')
        self.service.accept(2,first)

    def test_pagination_and_filter_validation(self):
        for index in range(3):
            self.pending(url=f'https://youtu.be/abcde{index:06d}')
        data = self.service.list_submissions(2,per_page=2,page=2,platform='youtube')
        self.assertEqual((data['total'],len(data['items']),data['pages']),(3,1,2))
        self.assertEqual(self.service.list_submissions(2,platform='spotify')['total'],0)
        for options in ({'status':'bad'},{'platform':'bad'},{'page':0},{'page':True},
                        {'page':999},{'per_page':101}):
            with self.subTest(options=options), self.assertRaises(SubmissionError):
                self.service.list_submissions(2,**options)

    def test_no_missing_record_or_bad_identifier(self):
        for sid in (999,0,True,'1'):
            with self.assertRaises(SubmissionError): self.service.get_submission(2,sid)

    def test_connection_requirements_and_no_implicit_commit(self):
        self.conn.execute("UPDATE users SET display_name='Pending' WHERE id=1")
        with self.assertRaises(SubmissionError): self.service.list_submissions(2)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.conn.execute('PRAGMA foreign_keys=OFF')
        with self.assertRaises(SubmissionError): self.service.list_submissions(2)

    def test_failed_decision_rolls_back_and_retry_commits_for_other_connection(self):
        sid = self.pending()
        self.conn.execute("""CREATE TRIGGER fail_review AFTER UPDATE OF status ON submissions
            BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END""")
        other = db.get_db(self.path)
        self.addCleanup(other.close)
        with self.assertRaises(sqlite3.IntegrityError): self.service.accept(2,sid)
        row = other.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone()
        self.assertEqual(row['status'],'pending_review')
        self.assertIsNone(row['reviewed_by'])
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute('DROP TRIGGER fail_review')
        self.service.accept(2,sid)
        self.assertEqual(other.execute('SELECT status FROM submissions WHERE id=?',(sid,)).fetchone()[0],
                         'approved_awaiting_audio')

    def test_concurrent_opposite_decisions_exactly_one_wins(self):
        sid = self.pending()
        barrier = threading.Barrier(2)
        def decide(accept):
            conn = db.get_db(self.path)
            try:
                barrier.wait(timeout=5)
                service = SubmissionReviewService(conn)
                try:
                    if accept: service.accept(2,sid)
                    else: service.reject(2,sid,reason='No')
                    return 'ok'
                except SubmissionError as error:
                    return error.code
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(decide,accept) for accept in (True,False)]
            results = [f.result(timeout=10) for f in futures]
        self.assertCountEqual(results,['ok','invalid_state'])
        self.assertIn(self.row(sid)['status'],('approved_awaiting_audio','rejected'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)

    def test_historical_tracks_untouched(self):
        sid = self.pending()
        for status in ('pending','approved','rejected'):
            self.conn.execute('''INSERT INTO tracks(uploader_id,file_path,title,genre_id,status)
                VALUES (1,?, 'Legacy',1,?)''',(f'{status}.mp3',status))
        self.conn.commit()
        before = [tuple(r) for r in self.conn.execute('SELECT * FROM tracks')]
        self.service.accept(2,sid)
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM tracks')],before)

    def test_audio_states_are_outside_url_review(self):
        sid = self.pending()
        self.service.accept(2,sid)
        tid = self.conn.execute('''INSERT INTO tracks(uploader_id,file_path,title,genre_id)
            VALUES (1,'fixture.mp3','Audio',1)''').lastrowid
        self.conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(tid,sid))
        self.conn.commit()
        for final in (False,True):
            if final: db.finalize_audio_review(self.conn,sid,2,approve=True)
            before = dict(self.row(sid))
            for operation in (lambda:self.service.accept(2,sid),
                              lambda:self.service.reject(2,sid,reason='No'),
                              lambda:self.service.update_editorial(2,sid,title='Other')):
                with self.assertRaises(SubmissionError): operation()
            self.assertEqual(dict(self.row(sid)),before)

    def test_accept_revalidates_incomplete_or_invalid_editorial(self):
        sid = self.pending()
        # SQLite trim() does not strip tabs; the service must validate beyond SQL CHECKs.
        for title in ('\t','bad\nvalue'):
            self.conn.execute('UPDATE submissions SET title=? WHERE id=?',(title,sid))
            self.conn.commit()
            with self.assertRaises(SubmissionError): self.service.accept(2,sid)
            self.assertIsNone(self.row(sid)['reviewed_by'])

    def test_failed_rejection_rolls_back_reason_and_review_identity(self):
        sid = self.pending()
        self.conn.execute("""CREATE TRIGGER fail_rejection AFTER UPDATE OF status ON submissions
            BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError): self.service.reject(2,sid,reason='No')
        row = self.row(sid)
        self.assertEqual(row['status'],'pending_review')
        self.assertIsNone(row['reviewed_by'])
        self.assertIsNone(row['rejection_reason'])

    def test_rejection_does_not_require_valid_editorial(self):
        sid = self.pending()
        self.conn.execute("UPDATE submissions SET title='bad' || char(10) || 'title' WHERE id=?",(sid,))
        self.conn.commit()
        self.service.reject(2,sid,reason='Invalid editorial content')
        self.assertEqual(self.row(sid)['status'],'rejected')
