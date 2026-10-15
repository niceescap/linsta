"""Contributor route tests using the isolated real-Flask fixture from lot 6A."""
import copy
import io
import os
import re
import unittest
from unittest.mock import Mock, patch

import requests

import test_flask_security as security
from submissions import SubmissionError, SubmissionService


VIDEO = 'dQw4w9WgXcQ'
URL = f'https://youtu.be/{VIDEO}'
SPOTIFY = 'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC'
PAYLOAD = {'items': [{'id': VIDEO, 'snippet': {'title': 'Provider title', 'channelTitle': 'Provider channel',
    'description': 'Provider description', 'thumbnails': {'high': {'url': 'https://i.ytimg.com/fake.jpg'}}},
    'contentDetails': {'duration': 'PT2M'}, 'statistics': {'viewCount': '0'}}]}


class FlaskSubmissionTests(unittest.TestCase):
    # Share setup/helpers, not inherited tests: each of the original 178 runs once.
    setUpClass = classmethod(security.FlaskSecurityTests.setUpClass.__func__)
    get = security.FlaskSecurityTests.get
    post = security.FlaskSecurityTests.post
    token = security.FlaskSecurityTests.token
    session_user = security.FlaskSecurityTests.session_user

    def setUp(self):
        security.FlaskSecurityTests.setUp(self)
        environment = patch.dict(os.environ, {'YOUTUBE_API_KEY':'fake-contributor-test-key',
                                               'SPOTIFY_METADATA_MODE':'disabled'})
        environment.start()
        self.addCleanup(environment.stop)
        self.http_response = Mock(status_code=200)
        self.http_response.json.return_value = copy.deepcopy(PAYLOAD)
        get = patch('requests.get',return_value=self.http_response)
        post = patch('requests.post',side_effect=AssertionError('No Spotify token request'))
        self.http, self.http_post = get.start(), post.start()
        self.addCleanup(get.stop)
        self.addCleanup(post.stop)
        self.service = SubmissionService(self.conn)
        self.session_user(1,role='contributor')

    def mutate(self,path,data=None,**kwargs):
        return self.post(path,data={'csrf_token':self.token(),**(data or {})},**kwargs)

    def draft(self,owner=1,url=URL):
        return self.service.create_draft(owner,url,fetch_metadata=False)['id']

    def complete(self,sid):
        self.service.update_editorial(1,sid,title='My title',artist='My artist',genre_id=1)

    def assert_no_track(self):
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)

    def test_dashboard_empty_and_home_link_preserves_mp3_form(self):
        result = self.get('/submissions')
        self.assertEqual(result.status_code,200)
        self.assertIn('Créer mon brouillon',result.get_data(as_text=True))
        self.assertIn('Page 1 sur 1',result.get_data(as_text=True))
        self.assertEqual(result.headers['Cache-Control'],'no-store')
        home = self.get('/').get_data(as_text=True)
        self.assertIn('href="/submissions"',home)
        self.assertIn('action="/tracks"',home)
        self.assertIn('name="file"',home)
        self.http.assert_not_called()

    def test_create_metadata_redirect_and_display(self):
        result = self.mutate('/submissions',{'source_url':URL})
        self.assertEqual(result.status_code,303)
        self.assertRegex(result.location,r'/submissions/[0-9]+$')
        detail = self.get(result.location)
        self.assertEqual(detail.status_code,200)
        text = detail.get_data(as_text=True)
        for value in ('Provider title','Provider channel','Provider description','120.0 secondes',
                      'Brouillon','Métadonnées disponibles','Utiliser ce titre','Vues YouTube'):
            self.assertIn(value,text)
        self.assertIn(f'href="{URL}"',text)
        self.assertNotIn('<img',text)  # No automatic artwork fetch, including Spotify.
        self.assertEqual(self.http.call_count,1)
        self.assert_no_track()

    def test_creation_uses_authenticated_identity_not_form_fields(self):
        result = self.mutate('/submissions',{'source_url':URL,'contributor_id':'2',
            'user_id':'2','status':'approved','track_id':'999'})
        self.assertEqual(result.status_code,303)
        row = self.conn.execute('SELECT * FROM submissions').fetchone()
        self.assertEqual(row['contributor_id'],1)
        self.assertEqual(row['status'],'draft')
        self.assertIsNone(row['track_id'])

    def test_creation_bad_url_and_duplicate(self):
        self.assertEqual(self.mutate('/submissions',{'source_url':'javascript:bad'}).status_code,400)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)
        self.draft()
        result = self.mutate('/submissions',{'source_url':f'https://www.youtube.com/watch?v={VIDEO}'})
        self.assertEqual(result.status_code,409)
        self.assertIn('soumission active',result.get_data(as_text=True))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],1)
        self.http.assert_not_called()

    def test_uploaded_file_rejected_on_url_creation(self):
        result = self.mutate('/submissions',{'source_url':URL,'file':(io.BytesIO(b'fake-audio'),'test.mp3')},
                             content_type='multipart/form-data')
        self.assertEqual(result.status_code,400)
        self.assert_no_track()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],0)

    def test_every_post_requires_csrf(self):
        sid = self.draft()
        for path in ('/submissions',f'/submissions/{sid}/editorial',
                     f'/submissions/{sid}/refresh-metadata',f'/submissions/{sid}/submit'):
            for token in (None,'wrong'):
                result = self.post(path,data={} if token is None else {'csrf_token':token})
                self.assertEqual(result.status_code,400)
                self.assertEqual(result.json['code'],'csrf_invalid')
        self.assertEqual(self.service.get_history(1,sid),[])
        self.http.assert_not_called()

    def test_anonymous_and_unverified_access(self):
        sid = self.draft()
        for uid in (None,3):
            with self.client.session_transaction(base_url=self.base) as session:
                session.clear()
                if uid:
                    session['user_id']=uid
            self.assertIn(self.get('/submissions').status_code,(401,403))
            self.assertIn(self.get(f'/submissions/{sid}').status_code,(401,403))
            self.assertEqual(self.mutate('/submissions',{'source_url':URL}).status_code,401)
        self.http.assert_not_called()

    def test_other_owner_not_exposed_on_get_or_post_even_to_admin(self):
        sid = self.draft(owner=2)
        self.service.update_editorial(2,sid,title='PRIVATE OTHER TITLE',artist='OTHER ARTIST',genre_id=1)
        for uid in (1,):
            self.session_user(uid)
            result = self.get(f'/submissions/{sid}')
            self.assertEqual(result.status_code,404)
            self.assertNotIn('PRIVATE OTHER TITLE',result.get_data(as_text=True))
            for action in ('editorial','refresh-metadata','submit'):
                result = self.mutate(f'/submissions/{sid}/{action}',{'title':'stolen','genre_id':'invalid'})
                self.assertEqual(result.status_code,404)
                self.assertNotIn('PRIVATE OTHER TITLE',result.get_data(as_text=True))
        listing = self.get('/submissions').get_data(as_text=True)
        self.assertNotIn('PRIVATE OTHER TITLE',listing)
        self.assertNotIn('OTHER ARTIST',listing)
        self.assertNotIn('user2@example.test',listing)
        self.http.assert_not_called()

    def test_admin_has_no_ownership_bypass_on_contributor_routes(self):
        sid = self.draft(owner=1)
        self.session_user(2,role='admin')
        self.assertEqual(self.get(f'/submissions/{sid}').status_code,404)
        self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,404)

    def test_dashboard_pagination_is_private_stable_and_bounded(self):
        ids=[]
        for i in range(21):
            sid=self.draft(url='https://youtu.be/'+f'{i:011d}')
            self.service.update_editorial(1,sid,title=f'Private own {i}',artist='A',genre_id=1)
            ids.append(sid)
        self.draft(owner=2,url='https://youtu.be/abcdefghijk')
        first=self.get('/submissions').get_data(as_text=True)
        second=self.get('/submissions?page=2').get_data(as_text=True)
        self.assertIn('Mes propositions (21)',first)
        self.assertIn('Page 1 sur 2',first)
        self.assertIn('Page suivante',first)
        self.assertIn('Private own 20',first)
        self.assertNotIn('Private own 0</a>',first)
        self.assertIn('Private own 0</a>',second)
        self.assertNotIn('Private own 20',second)
        self.assertIn('Page précédente',second)
        page=self.service.list_submissions(1,page=1)
        self.assertEqual([r['id'] for r in page['items']],list(reversed(ids))[0:20])
        self.assertFalse(self.conn.in_transaction)
        self.http.assert_not_called()

    def test_bad_pages(self):
        for page in ('0','-1','abc','1.5','9999999999999'):
            self.assertEqual(self.get('/submissions?page='+page).status_code,400)
        self.assertEqual(self.get('/submissions?page=2').status_code,404)
        for page in (True,'1',0):
            with self.assertRaises(SubmissionError):
                self.service.list_submissions(1,page=page)
        with self.assertRaises(SubmissionError):
            self.service.list_submissions(1,per_page=101)

    def test_editorial_save_redirect_and_submit(self):
        sid=self.draft()
        result=self.mutate(f'/submissions/{sid}/editorial',{'title':' Corrected title ',
            'artist':' Corrected artist ','genre_id':'2','status':'approved'})
        self.assertEqual(result.status_code,303)
        detail=self.get(result.location).get_data(as_text=True)
        self.assertIn('value="Corrected title"',detail)
        self.assertIn('value="Corrected artist"',detail)
        self.assertIn('Informations enregistrées.',detail)
        self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,303)
        row=self.service.get_submission(1,sid)
        self.assertEqual((row['status'],row['genre_id']),('pending_review',2))
        self.assertIsNone(row['reviewed_by'])
        self.assertIsNone(row['track_id'])
        self.assert_no_track()

    def test_invalid_genre_and_editorial_error_preserves_user_input(self):
        sid=self.draft()
        for genre in ('999','-1','1.5','true','9223372036854775808'):
            result=self.mutate(f'/submissions/{sid}/editorial',{'title':'Keep my text','artist':'Artist','genre_id':genre})
            self.assertEqual(result.status_code,400)
            self.assertIn('Keep my text',result.get_data(as_text=True))
        self.assertIsNone(self.service.get_submission(1,sid)['title'])
        result=self.mutate(f'/submissions/{sid}/editorial',{'title':'bad\x00text'})
        self.assertEqual(result.status_code,400)

    def test_incomplete_submission_rejected_on_server(self):
        sid=self.draft()
        result=self.mutate(f'/submissions/{sid}/submit')
        self.assertEqual(result.status_code,400)
        self.assertIn('Titre, artiste et genre sont obligatoires',result.get_data(as_text=True))
        self.assertEqual(self.service.get_submission(1,sid)['status'],'draft')
        self.assert_no_track()

    def test_manual_fields_preserved_after_refresh_and_suggestions_not_auto_applied(self):
        sid=self.draft()
        self.complete(sid)
        self.assertEqual(self.mutate(f'/submissions/{sid}/refresh-metadata').status_code,303)
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertIn('value="My title"',text)
        self.assertIn('value="My artist"',text)
        self.assertIn('Suggestion : Provider title',text)
        self.assertNotIn('Utiliser cet artiste',text)
        self.service.update_editorial(1,sid,title=None)
        self.mutate(f'/submissions/{sid}/refresh-metadata')
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertIn('id="editorial-title" type="text" name="title" value=""',text)

    def test_spotify_disabled_manual_path_no_network_or_artwork(self):
        result=self.mutate('/submissions',{'source_url':SPOTIFY})
        self.assertEqual(result.status_code,303)
        text=self.get(result.location).get_data(as_text=True)
        self.assertIn('récupération Spotify est désactivée',text)
        self.assertNotIn('SPOTIFY_CLIENT_SECRET',text)
        sid=self.conn.execute('SELECT id FROM submissions').fetchone()[0]
        self.complete(sid)
        self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,303)
        self.assert_no_track()
        self.http.assert_not_called()
        self.http_post.assert_not_called()

    def test_partial_metadata_and_errors_are_readable(self):
        self.http_response.json.return_value={'items':[{'id':VIDEO}]}
        result=self.mutate('/submissions',{'source_url':URL})
        self.assertIn('Métadonnées partielles',self.get(result.location).get_data(as_text=True))
        sid=self.conn.execute('SELECT id FROM submissions').fetchone()[0]
        self.http.side_effect=requests.Timeout('PRIVATE PROVIDER MESSAGE')
        result=self.mutate(f'/submissions/{sid}/refresh-metadata')
        text=self.get(result.location).get_data(as_text=True)
        self.assertIn('ne répond pas à temps',text)
        self.assertNotIn('PRIVATE PROVIDER MESSAGE',text)
        self.complete(sid)
        self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,303)

    def test_provider_html_is_escaped_in_text_and_suggestion_attributes(self):
        attack='<img src=x onerror="alert(1)"><script>alert(2)</script>'
        self.http_response.json.return_value['items'][0]['snippet']['title']=attack
        self.http_response.json.return_value['items'][0]['snippet']['description']=attack
        result=self.mutate('/submissions',{'source_url':URL})
        text=self.get(result.location).get_data(as_text=True)
        self.assertNotIn('<img src=x',text)
        self.assertNotIn('<script>alert(2)',text)
        self.assertIn('&lt;img',text)
        self.assertIn('data-suggestion="&lt;img',text)

    def test_unsafe_stored_links_are_not_clickable(self):
        sid=self.draft()
        self.conn.execute('UPDATE submissions SET artwork_url=? WHERE id=?',('javascript:alert(1)',sid))
        self.conn.commit()
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertNotIn('href="javascript:',text)

    def test_all_new_forms_have_csrf_and_explicit_post_actions(self):
        sid=self.draft()
        for route in ('/submissions',f'/submissions/{sid}'):
            text=self.get(route).get_data(as_text=True)
            forms=re.findall(r'<form\b.*?</form>',text,flags=re.S)
            self.assertTrue(forms)
            for form in forms:
                self.assertIn('name="csrf_token"',form)
                self.assertIn('method="post"',form)
            self.assertNotIn('type="file"',text)

    def test_pending_review_is_readonly_and_cannot_be_submitted_twice(self):
        sid=self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertIn('En attente de validation',text)
        self.assertNotIn('name="title"',text)
        self.assertNotIn('Envoyer pour validation',text)
        self.assertEqual(self.mutate(f'/submissions/{sid}/editorial',{'title':'Changed'}).status_code,409)
        self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,409)
        self.assertEqual(self.service.get_submission(1,sid)['title'],'My title')

    def test_accepted_state_shows_audio_step_without_upload(self):
        sid=self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        self.conn.execute("""UPDATE submissions SET status='approved_awaiting_audio',reviewed_by=2,
            reviewed_at=CURRENT_TIMESTAMP WHERE id=?""",(sid,))
        self.conn.commit()
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertIn('Acceptée — MP3 attendu',text)
        self.assertNotIn('type="file"',text)
        self.assertNotIn('/refresh-metadata',text)
        self.assertEqual(self.mutate(f'/submissions/{sid}/refresh-metadata').status_code,409)
        self.assert_no_track()

    def test_rejected_reason_escaped_and_new_attempt_allowed(self):
        sid=self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        self.conn.execute("""UPDATE submissions SET status='rejected',reviewed_by=2,
            reviewed_at=CURRENT_TIMESTAMP,rejection_reason='<script>private()</script>' WHERE id=?""",(sid,))
        self.conn.commit()
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertIn('Refusée',text)
        self.assertNotIn('<script>private()',text)
        self.assertIn('&lt;script&gt;private()',text)
        self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,409)
        self.assertEqual(self.mutate('/submissions',{'source_url':URL}).status_code,303)

    def test_connections_closed_on_success_and_validation_errors(self):
        sid=self.draft()
        opened=[]
        def factory():
            conn=security.db.get_db(self.path)
            opened.append(conn)
            return conn
        with patch.object(self.module,'get_db',side_effect=factory):
            self.assertEqual(self.get(f'/submissions/{sid}').status_code,200)
            self.assertEqual(self.mutate(f'/submissions/{sid}/editorial',{'genre_id':'bad'}).status_code,400)
        for conn in opened:
            with self.assertRaises(security.sqlite3.ProgrammingError):
                conn.execute('SELECT 1')

    def test_gets_never_fetch_metadata_or_change_generation(self):
        sid=self.draft()
        before=dict(self.conn.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone())
        self.get('/submissions')
        self.get(f'/submissions/{sid}')
        after=dict(self.conn.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone())
        self.assertEqual(before,after)
        self.http.assert_not_called()
        self.assertEqual(self.service.get_history(1,sid),[])

    def test_deleted_account_cannot_access_dashboard(self):
        self.conn.execute('DELETE FROM users WHERE id=1')
        self.conn.commit()
        self.assertEqual(self.get('/submissions').status_code,401)

    def test_cross_contributor_duplicate_warning_not_rendered_to_artist(self):
        self.draft(owner=2)
        sid=self.draft()
        text=self.get(f'/submissions/{sid}').get_data(as_text=True)
        self.assertNotIn('user2@example.test',text)
        self.assertNotIn('has_other_contributor_source',text)

    def test_audio_states_render_readonly_without_new_audio_routes(self):
        sid=self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        self.conn.execute("""UPDATE submissions SET status='approved_awaiting_audio',reviewed_by=2,
            reviewed_at=CURRENT_TIMESTAMP WHERE id=?""",(sid,))
        tid=self.conn.execute("""INSERT INTO tracks(uploader_id,file_path,title,genre_id,status)
            VALUES (1,'tracks/fixture.mp3','Fixture audio',1,'pending')""").lastrowid
        self.conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(tid,sid))
        self.conn.commit()
        for label in ('Audio en attente de validation','Audio validé'):
            if label=='Audio validé':
                security.db.finalize_audio_review(self.conn,sid,2,approve=True)
            text=self.get(f'/submissions/{sid}').get_data(as_text=True)
            self.assertIn(label,text)
            self.assertNotIn('name="title"',text)
            self.assertNotIn('type="file"',text)
            self.assertEqual(self.mutate(f'/submissions/{sid}/submit').status_code,409)
            self.assertEqual(self.mutate(f'/submissions/{sid}/editorial',{'title':'Changed'}).status_code,409)
            self.assertEqual(self.mutate(f'/submissions/{sid}/refresh-metadata').status_code,409)
        self.assertEqual(self.get(f'/submissions/{sid}/audio').status_code,404)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
