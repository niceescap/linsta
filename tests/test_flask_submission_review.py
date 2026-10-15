"""Admin review HTTP tests with temporary DB and blocked external services."""
import io
import re
import unittest
from unittest.mock import patch

from werkzeug.datastructures import MultiDict
import db
import test_flask_submissions as contributor
from submissions import SubmissionService
from submission_review import SubmissionReviewService


class FlaskReviewTests(unittest.TestCase):
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

    def pending(self, owner=1):
        sid = self.draft(owner=owner)
        self.service.update_editorial(owner,sid,title='Editorial title',artist='Editorial artist',genre_id=1)
        self.service.submit_for_review(owner,sid)
        self.session_user(2)
        return sid

    def route(self,sid,suffix=''):
        return f'/admin/submissions/{sid}{suffix}'

    def test_queue_and_admin_link(self):
        sid = self.pending()
        result = self.get('/admin/submissions')
        self.assertEqual(result.status_code,200)
        self.assertIn('Editorial title',result.get_data(as_text=True))
        self.assertIn(self.route(sid),result.get_data(as_text=True))
        self.assertIn('href="/admin/submissions"',self.get('/admin').get_data(as_text=True))
        self.assertEqual(result.headers['Cache-Control'],'no-store')
        self.http.assert_not_called()

    def test_permissions_on_all_routes(self):
        sid = self.pending()
        for uid, expected in ((None,401),(1,403),(3,403)):
            with self.client.session_transaction(base_url=self.base) as session: session.clear()
            if uid: self.session_user(uid)
            for path in ('/admin/submissions',self.route(sid)):
                if uid: self.session_user(uid)
                self.assertEqual(self.get(path).status_code,expected)
            for action in ('editorial','accept','reject'):
                # Unverified accounts invalidate the session before CSRF runs;
                # their old token is consequently rejected with 400.
                token = self.token()
                if uid: self.session_user(uid)
                self.assertEqual(self.post(self.route(sid,'/'+action),
                    data={'csrf_token':token,'reason':'No'}).status_code,400 if uid == 3 else expected)
        self.assert_no_track()

    def test_demoted_session_cannot_review(self):
        sid = self.pending()
        self.conn.execute("UPDATE users SET role='contributor' WHERE id=2")
        self.conn.commit()
        self.assertEqual(self.get('/admin/submissions').status_code,403)
        self.assertEqual(self.mutate(self.route(sid,'/accept')).status_code,403)
        self.assertEqual(self.conn.execute('SELECT status FROM submissions').fetchone()[0],'pending_review')

    def test_service_rechecks_role_after_request_account_load(self):
        sid = self.pending()
        original = SubmissionReviewService.accept
        def demote(service, actor, submission_id):
            service.conn.execute("UPDATE users SET role='contributor' WHERE id=2")
            service.conn.commit()
            return original(service,actor,submission_id)
        with patch.object(SubmissionReviewService,'accept',demote):
            self.assertEqual(self.mutate(self.route(sid,'/accept')).status_code,403)

    def test_csrf_for_every_admin_mutation(self):
        sid = self.pending()
        for action in ('editorial','accept','reject'):
            for data in ({},{'csrf_token':'invalid'}):
                self.assertEqual(self.post(self.route(sid,'/'+action),data=data).status_code,400)
        self.assertIsNone(self.conn.execute('SELECT reviewed_at FROM submissions').fetchone()[0])

    def test_detail_forms_have_tokens_and_no_audio(self):
        sid = self.pending()
        result = self.get(self.route(sid))
        text = result.get_data(as_text=True)
        forms = re.findall(r'<form\b.*?</form>',text,re.S)
        self.assertEqual(len(forms),3)
        for form in forms: self.assertIn('name="csrf_token"',form)
        for forbidden in ('<audio','<img','type="file"','/tracks/'):
            self.assertNotIn(forbidden,text)
        self.assert_no_track()

    def test_admin_correction_is_allowlisted(self):
        sid = self.pending()
        result = self.mutate(self.route(sid,'/editorial'),{'title':'Corrected', 'artist':'Changed',
            'genre_id':'2','status':'approved','contributor_id':'2','source_title':'Injected','reviewed_by':'1'})
        self.assertEqual(result.status_code,303)
        row = self.conn.execute('SELECT * FROM submissions').fetchone()
        self.assertEqual((row['title'],row['artist'],row['genre_id']),('Corrected','Changed',2))
        self.assertEqual(row['status'],'pending_review')
        self.assertEqual(row['contributor_id'],1)
        self.assertIsNone(row['source_title'])
        self.assertIsNone(row['reviewed_by'])
        self.assert_no_track()

    def test_invalid_corrections_render_errors_and_rollback(self):
        sid = self.pending()
        for fields in ({'title':''},{'genre_id':'999'},{'genre_id':'bad'},{'genre_id':'0'},
                       {'genre_id':''},{'artist':'bad\nvalue'}):
            result = self.mutate(self.route(sid,'/editorial'),fields)
            self.assertEqual(result.status_code,400)
            self.assertIn('role="alert"',result.get_data(as_text=True))
            self.assertEqual(self.conn.execute('SELECT title FROM submissions').fetchone()[0],'Editorial title')

    def test_accept_uses_current_admin_and_only_url_state(self):
        sid = self.pending()
        result = self.mutate(self.route(sid,'/accept'),{'reviewed_by':'1','status':'approved','track_id':'1'})
        self.assertEqual(result.status_code,303)
        row = self.conn.execute('SELECT * FROM submissions').fetchone()
        self.assertEqual((row['status'],row['reviewed_by']),('approved_awaiting_audio',2))
        self.assertTrue(row['reviewed_at'])
        self.assertIsNone(row['audio_reviewed_by'])
        self.assertIsNone(row['track_id'])
        self.assert_no_track()
        text = self.get(result.location).get_data(as_text=True)
        self.assertIn('n’est pas diffusable',text)
        self.assertNotIn('action="'+self.route(sid,'/accept')+'"',text)

    def test_reject_requires_reason_and_preserves_history(self):
        sid = self.pending()
        for reason in ('','   ','bad\nreason'):
            self.assertEqual(self.mutate(self.route(sid,'/reject'),{'reason':reason}).status_code,400)
        result = self.mutate(self.route(sid,'/reject'),{'reason':'Mismatch'})
        self.assertEqual(result.status_code,303)
        self.assertIn('Mismatch',self.get(result.location).get_data(as_text=True))
        self.assertEqual(self.conn.execute('SELECT status FROM submissions').fetchone()[0],'rejected')
        self.session_user(1)
        self.assertIn('Mismatch',self.get(f'/submissions/{sid}').get_data(as_text=True))
        self.assert_no_track()

    def test_repeated_decisions_and_editorial_conflict(self):
        sid = self.pending()
        self.mutate(self.route(sid,'/accept'))
        before = dict(self.conn.execute('SELECT * FROM submissions').fetchone())
        for action in ('accept','reject','editorial'):
            result = self.mutate(self.route(sid,'/'+action),{'reason':'Other','title':'Other'})
            self.assertEqual(result.status_code,409)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM submissions').fetchone()),before)

    def test_drafts_read_only_and_absent_from_default_queue(self):
        sid = self.draft()
        self.session_user(2)
        self.assertNotIn(self.route(sid),self.get('/admin/submissions').get_data(as_text=True))
        self.assertIn(self.route(sid),self.get('/admin/submissions?status=draft').get_data(as_text=True))
        for action in ('accept','reject','editorial'):
            self.assertEqual(self.mutate(self.route(sid,'/'+action),{'reason':'No'}).status_code,409)

    def test_filters_pages_and_invalid_queries(self):
        for index in range(21):
            sid = self.service.create_draft(1,f'https://youtu.be/abcde{index:06d}',fetch_metadata=False)['id']
            self.complete(sid)
            self.service.submit_for_review(1,sid)
        self.session_user(2)
        text = self.get('/admin/submissions?platform=youtube').get_data(as_text=True)
        self.assertIn('Page 1 sur 2',text)
        self.assertIn('platform=youtube',text)
        self.assertIn('Page 2 sur 2',self.get('/admin/submissions?page=2&platform=youtube').get_data(as_text=True))
        for query in ('status=bad','platform=bad','page=-1','page=abc','page=0'):
            self.assertEqual(self.get('/admin/submissions?'+query).status_code,400)
        self.assertEqual(self.get('/admin/submissions?page=999').status_code,404)
        self.assertIn('Aucune proposition',self.get('/admin/submissions?platform=spotify').get_data(as_text=True))

    def test_duplicates_displayed_to_admin_only_and_do_not_block(self):
        sid = self.pending()
        self.conn.execute("UPDATE users SET is_verified=1 WHERE id=3")
        self.conn.commit()
        other = self.draft(owner=3)
        self.assertIn('autre contributeur',self.get('/admin/submissions').get_data(as_text=True))
        self.assertIn(self.route(other),self.get(self.route(sid)).get_data(as_text=True))
        self.assertEqual(self.mutate(self.route(sid,'/accept')).status_code,303)
        self.session_user(1)
        text = self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertNotIn(self.route(other),text)
        self.assert_no_track()

    def test_provider_and_editorial_html_escaped_no_unsafe_links(self):
        sid = self.pending()
        evil = '<script>alert("bad")</script>'
        self.conn.execute('''UPDATE submissions SET source_title=?,source_description=?,artist_name=?,
            channel_name=?,artwork_url='javascript:alert(1)',title=? WHERE id=?''', (evil,evil,evil,evil,evil,sid))
        self.conn.commit()
        text = self.get(self.route(sid)).get_data(as_text=True)
        self.assertNotIn('<script>',text)
        self.assertIn('&lt;script&gt;',text)
        self.assertNotIn('href="javascript:',text)
        self.assertNotIn('<img',text)
        self.assertNotIn('<script>',self.get('/admin/submissions').get_data(as_text=True))
        self.assertEqual(self.mutate(self.route(sid,'/reject'),{'reason':evil}).status_code,303)
        self.assertNotIn('<script>',self.get(self.route(sid)).get_data(as_text=True))

    def test_spotify_artwork_link_only_no_requests(self):
        sid = self.service.create_draft(1,contributor.SPOTIFY,fetch_metadata=False)['id']
        self.conn.execute("UPDATE submissions SET artwork_url='https://i.scdn.co/test.jpg' WHERE id=?",(sid,))
        self.conn.commit()
        self.session_user(2)
        text = self.get(self.route(sid)).get_data(as_text=True)
        self.assertIn('href="https://i.scdn.co/test.jpg"',text)
        self.assertNotIn('<img',text)
        self.http.assert_not_called()
        self.http_post.assert_not_called()

    def test_unknown_record_404(self):
        self.session_user(2)
        self.assertEqual(self.get(self.route(999)).status_code,404)
        for action in ('editorial','accept','reject'):
            self.assertEqual(self.mutate(self.route(999,'/'+action)).status_code,404)

    def test_duplicate_fields_and_files_rejected(self):
        sid = self.pending()
        for action,field in (('reject','reason'),('editorial','title')):
            data = MultiDict([('csrf_token',self.token()),(field,'First'),(field,'Other')])
            self.assertEqual(self.post(self.route(sid,'/'+action),data=data).status_code,400)
        self.assertEqual(self.mutate(self.route(sid,'/accept'),{'file':(io.BytesIO(b'fake'),'bad.mp3')},
            content_type='multipart/form-data').status_code,400)
        self.assert_no_track()

    def test_connections_close_no_automatic_migrations(self):
        sid = self.pending()
        opened = []
        def factory():
            conn = db.get_db(self.path)
            opened.append(conn)
            return conn
        with patch.object(self.module,'get_db',side_effect=factory), \
                patch.object(db,'migrate_db',side_effect=AssertionError('No real migration')), \
                patch.object(db,'init_db',side_effect=AssertionError('No initialization')):
            self.assertEqual(self.get('/admin/submissions').status_code,200)
            self.assertEqual(self.get(self.route(sid)).status_code,200)
            self.assertEqual(self.mutate(self.route(sid,'/accept')).status_code,303)
        import sqlite3
        for conn in opened:
            with self.assertRaises(sqlite3.ProgrammingError): conn.execute('SELECT 1')
