"""End-to-end Flask/service deposit and review with real local parser validation.

Synthetic MPEG frames and Pillow fixtures only. No production config, DB, media,
network or FFmpeg. A production-approved document is simulated only in tests
which explicitly patch the otherwise empty rights registry.
"""
import concurrent.futures
import hashlib
import io
from html.parser import HTMLParser
import re
from pathlib import Path
import sqlite3
import shutil
import threading
import unittest
from dataclasses import replace
from unittest.mock import patch

from flask import Request
from werkzeug.datastructures import MultiDict

import db
import rights
from audio_storage import MediaLimits, StorageSession, orphan_candidates
from submission_audio import SubmissionAudioService
from submission_review import SubmissionReviewService
from submissions import SubmissionError
import test_flask_submissions as fixture
from test_media_storage import mp3_bytes,cover_bytes


class AudioWorkflowTests(unittest.TestCase):
    setUpClass=classmethod(fixture.FlaskSubmissionTests.setUpClass.__func__)
    get=fixture.FlaskSubmissionTests.get
    post=fixture.FlaskSubmissionTests.post
    token=fixture.FlaskSubmissionTests.token
    session_user=fixture.FlaskSubmissionTests.session_user
    mutate=fixture.FlaskSubmissionTests.mutate
    draft=fixture.FlaskSubmissionTests.draft
    complete=fixture.FlaskSubmissionTests.complete

    def setUp(self):
        fixture.FlaskSubmissionTests.setUp(self)
        keys=('MEDIA_LIMITS','MAX_CONTENT_LENGTH')
        config={key:self.app.config[key] for key in keys}
        self.addCleanup(self.app.config.update,config)
        self.audio_service=SubmissionAudioService(self.conn,self.temp.name,self.temp.name)

    def accepted(self):
        sid=self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        SubmissionReviewService(self.conn).accept(2,sid)
        return sid

    def form(self,**changes):
        values=dict(audio=(io.BytesIO(mp3_bytes()),'track.mp3'),cover=(io.BytesIO(cover_bytes()),'cover.png'),
            declared_name='Declarant',declared_artist='My artist',declarant_capacity='rights_holder',
            music_accepted='on',visual_accepted='on',document_version=rights.DRAFT_DOCUMENT.version,
            document_sha256=rights.DRAFT_DOCUMENT.sha256)
        values.update(changes)
        return values

    def upload(self,sid,**changes):
        return self.mutate(f'/submissions/{sid}/audio',self.form(**changes),content_type='multipart/form-data')

    def deposited(self):
        sid=self.accepted()
        result=self.upload(sid)
        self.assertEqual(result.status_code,303,result.get_data(as_text=True))
        return sid

    def row(self,sid):
        return self.conn.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone()

    def proof(self,sid): return self.audio_service._evidence(sid)

    def final(self,sid,action='approve',**fields):
        self.session_user(2)
        return self.mutate(f'/admin/submissions/{sid}/audio/{action}',fields)

    def generated(self):
        return [p for p in Path(self.temp.name).rglob('digger-*') if p.is_file()]

    def assert_no_deposit(self,sid):
        self.assertEqual(self.row(sid)['status'],'approved_awaiting_audio')
        for table in ('tracks','submission_media_bundles','submission_authorizations'):
            self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
        self.assertEqual(self.generated(),[])

    def legal_simulation(self):
        return patch.object(rights,'_PRODUCTION_VERSIONS',frozenset([rights.DRAFT_DOCUMENT.version]))

    def test_complete_deposit_persists_three_files_and_two_acceptances(self):
        sid=self.deposited()
        evidence=self.proof(sid)
        self.assertEqual(self.row(sid)['status'],'pending_audio_review')
        track=self.conn.execute('SELECT * FROM tracks').fetchone()
        self.assertEqual(track['status'],'pending')
        self.assertEqual((track['title'],track['artist']),('My title','My artist'))
        self.assertEqual(len(self.generated()),3)
        for prefix,root in (('audio',Path(self.temp.name)),('cover_original',Path(self.temp.name)),('cover_normalized',Path(self.temp.name))):
            path=root/evidence[prefix+'_path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),evidence[prefix+'_sha256'])
            self.assertEqual(path.stat().st_size,evidence[prefix+'_size'])
        self.assertEqual((evidence['music_accepted'],evidence['visual_accepted']),(1,1))
        self.assertEqual(evidence['document_text'],rights.DRAFT_DOCUMENT.text)
        self.http.assert_not_called()
        self.http_post.assert_not_called()

    def test_contributor_form_is_conditional_and_has_no_provider_cover_copy(self):
        sid=self.draft()
        self.assertEqual(self.get(f'/submissions/{sid}/audio').status_code,409)
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        self.assertEqual(self.get(f'/submissions/{sid}/audio').status_code,409)
        SubmissionReviewService(self.conn).accept(2,sid)
        page=self.get(f'/submissions/{sid}/audio')
        self.assertEqual(page.status_code,200)
        text=page.get_data(as_text=True)
        for field in ('audio','cover','music_accepted','visual_accepted','csrf_token','document_sha256'):
            self.assertIn('name="'+field+'"',text)
        self.assertNotIn('checked',text)
        self.assertIn('Version bêta — Conditions de diffusion en cours de validation juridique',text)
        self.assertIn(rights.DRAFT_DOCUMENT.music_clause,text)
        self.assertIn(rights.DRAFT_DOCUMENT.visual_clause,text)
        self.assertIn('n’est pas le document accepté',text)
        self.assertNotIn('i.ytimg.com',text)

    def test_informational_conditions_preserve_full_v03_and_current_consent(self):
        sid=self.accepted()
        before=[line for line in self.conn.iterdump()]
        page=self.get(f'/submissions/{sid}/audio')
        form=page.get_data(as_text=True)
        conditions_url=f'/submissions/{sid}/audio/conditions'
        self.assertIn(f'href="{conditions_url}"',form)
        self.assertIn('Consulter les conditions de diffusion',form)
        self.assertIn('value="draft-media-v1"',form)
        self.assertIn('value="'+rights.DRAFT_DOCUMENT.sha256+'"',form)
        self.assertIn('Je confirme explicitement la déclaration musicale du document affiché pour le MP3 déposé.',form)
        self.assertIn('Je confirme explicitement la déclaration visuelle du même document pour la cover déposée.',form)
        self.assertEqual(form.count('type="checkbox"'),2)
        self.assertNotIn('checked',form)
        response=self.get(conditions_url)
        self.assertEqual(response.status_code,200)
        text=response.get_data(as_text=True)
        self.assertIn('Version bêta — Document en cours de validation juridique',text)
        self.assertIn(f'href="/submissions/{sid}/audio"',text)
        self.assertIn('Retour au dépôt',text)
        self.assertEqual(text.count('<h3>'),11)
        self.assertNotIn('<form',text)
        self.assertNotIn('type="checkbox"',text)
        class PlainText(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts=[]
            def handle_data(self,data): self.parts.append(data)
        parser=PlainText()
        parser.feed(re.search(r'<article id="contract-v03">(.*?)</article>',text,re.S).group(1))
        original=(Path(__file__).resolve().parents[1]/'documents/digger-radio-v0.3.md').read_text()
        original=re.sub(r'(?m)^(?:#{1,3} |- )','',original).replace('**','')
        self.assertEqual(' '.join(''.join(parser.parts).split()),' '.join(original.split()))
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual([line for line in self.conn.iterdump()],before)
        self.assertEqual(rights.DRAFT_DOCUMENT.version,'draft-media-v1')
        self.assertFalse(rights.document_is_production_ready(rights.DRAFT_DOCUMENT.version,
            rights.DRAFT_DOCUMENT.text,rights.DRAFT_DOCUMENT.sha256))

    def test_informational_conditions_require_verified_owner(self):
        sid=self.accepted()
        for uid,expected in ((None,401),(3,403),(2,404)):
            with self.client.session_transaction(base_url=self.base) as session: session.clear()
            if uid: self.session_user(uid)
            self.assertEqual(self.get(f'/submissions/{sid}/audio/conditions').status_code,expected)

    def test_permissions_precede_multipart_parsing(self):
        sid=self.accepted()
        for uid,expected in ((None,401),(3,403),(2,404)):
            with self.client.session_transaction(base_url=self.base) as session: session.clear()
            if uid:self.session_user(uid)
            with patch.object(Request,'_load_form_data',side_effect=AssertionError('Body must not be parsed')):
                result=self.post(f'/submissions/{sid}/audio',data=b'not parsed',content_type='multipart/form-data')
            self.assertEqual(result.status_code,expected)
        self.assert_no_deposit(sid)

    def test_rejected_source_never_opens_upload(self):
        sid=self.draft()
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        SubmissionReviewService(self.conn).reject(2,sid,reason='Source refused')
        self.assertEqual(self.upload(sid).status_code,409)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)
        self.assertEqual(self.generated(),[])

    def test_missing_audio_or_cover_and_missing_acceptances_leave_no_files(self):
        sid=self.accepted()
        for changes in ({'audio':(io.BytesIO(b''),'')},{'cover':(io.BytesIO(b''),'')},
                        {'music_accepted':''},{'visual_accepted':''}):
            self.assertEqual(self.upload(sid,**changes).status_code,400)
            self.assert_no_deposit(sid)

    def test_csrf_all_mutations_and_explicit_valid_header_upload(self):
        sid=self.accepted()
        self.assertEqual(self.post(f'/submissions/{sid}/audio',data=self.form()).status_code,400)
        self.assertEqual(self.post(f'/submissions/{sid}/audio',data={**self.form(),'csrf_token':'bad'}).status_code,400)
        self.assert_no_deposit(sid)
        self.assertEqual(self.post(f'/submissions/{sid}/audio',data=self.form(),
            headers={'X-CSRF-Token':self.token()}).status_code,303)
        self.session_user(2)
        for action in ('approve','reject'):
            self.assertEqual(self.post(f'/admin/submissions/{sid}/audio/{action}').status_code,400)

    def test_document_and_identity_tampering_rejected(self):
        sid=self.accepted()
        for changes in ({'document_version':'approved'},{'document_sha256':'0'*64},
                        {'declared_name':'\t'},{'declarant_capacity':'unknown'}):
            self.assertEqual(self.upload(sid,**changes).status_code,400)
            self.assert_no_deposit(sid)

    def test_invalid_audio_or_image_cleanup(self):
        sid=self.accepted()
        for changes in ({'audio':(io.BytesIO(b'fake'),'fake.mp3')},
                        {'cover':(io.BytesIO(b'<svg/>'),'cover.png')},
                        {'cover':(io.BytesIO(cover_bytes('JPEG')),'cover.png')}):
            self.assertEqual(self.upload(sid,**changes).status_code,400)
            self.assert_no_deposit(sid)

    def test_limits_applied_in_service_and_before_parsing(self):
        sid=self.accepted()
        self.app.config['MEDIA_LIMITS']=replace(MediaLimits(),audio_bytes=10)
        self.assertEqual(self.upload(sid).status_code,413)
        self.assert_no_deposit(sid)
        self.app.config['MAX_CONTENT_LENGTH']=100
        self.assertEqual(self.upload(sid).status_code,413)
        self.assert_no_deposit(sid)

    def test_duplicate_file_and_acceptance_fields_rejected(self):
        sid=self.accepted()
        data=MultiDict(self.form())
        data.add('audio',(io.BytesIO(mp3_bytes()),'second.mp3'))
        data.add('csrf_token',self.token())
        self.assertEqual(self.post(f'/submissions/{sid}/audio',data=data).status_code,400)
        data=MultiDict(self.form())
        data.add('visual_accepted','on')
        # mutate uses dict unpacking; submit MultiDict directly to preserve duplicates.
        data.add('csrf_token',self.token())
        self.assertEqual(self.post(f'/submissions/{sid}/audio',data=data).status_code,400)
        self.assert_no_deposit(sid)

    def test_repeat_upload_refused_before_parsing_preserves_first_files(self):
        sid=self.deposited()
        before={str(p):p.read_bytes() for p in self.generated()}
        token=self.token()
        with patch.object(Request,'_load_form_data',side_effect=AssertionError('Already deposited')):
            response=self.post(f'/submissions/{sid}/audio',data=b'ignored',headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,409)
        self.assertEqual({str(p):p.read_bytes() for p in self.generated()},before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],1)

    def test_private_media_owner_and_admin_only_public_cover_hidden(self):
        sid=self.deposited()
        tid=self.row(sid)['track_id']
        for kind in ('audio','cover'):
            response=self.get(f'/submissions/{sid}/audio/media/{kind}')
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.data)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            response.close()
        self.session_user(2)
        for kind in ('audio','cover'):
            response=self.get(f'/admin/submissions/{sid}/audio/media/{kind}')
            self.assertEqual(response.status_code,200)
            response.data
            response.close()
        self.session_user(3)
        self.assertEqual(self.get(f'/submissions/{sid}/audio/media/cover').status_code,403)
        with self.client.session_transaction(base_url=self.base) as session:session.clear()
        self.assertEqual(self.get(f'/media/cover/{tid}').status_code,404)
        self.assertEqual(self.get(f'/submissions/{sid}/audio/media/audio').status_code,401)

    def test_other_verified_contributor_cannot_access_media_or_evidence(self):
        sid=self.deposited()
        self.conn.execute('UPDATE users SET is_verified=1 WHERE id=3')
        self.conn.commit()
        self.session_user(3)
        for path in (f'/submissions/{sid}/audio',f'/submissions/{sid}/audio/media/audio',f'/submissions/{sid}/audio/media/cover'):
            result=self.get(path)
            self.assertEqual(result.status_code,404)
            self.assertNotIn('Declarant',result.get_data(as_text=True))
        self.assertEqual(self.get(f'/admin/submissions/{sid}/audio').status_code,403)

    def test_admin_page_and_both_confirmations_required(self):
        sid=self.deposited()
        self.session_user(2)
        text=self.get(f'/admin/submissions/{sid}/audio').get_data(as_text=True)
        self.assertIn('<audio',text)
        self.assertIn('<img',text)
        self.assertIn('Approbation bloquée',text)
        self.assertIn('disabled',text)
        self.assertEqual(self.final(sid).status_code,400)
        self.assertEqual(self.final(sid,listened='on').status_code,400)
        self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)
        self.assertEqual(self.row(sid)['status'],'pending_audio_review')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tracks WHERE status='approved'").fetchone()[0],0)

    def test_complete_final_approval_with_test_only_legal_registry(self):
        sid=self.deposited()
        tid=self.row(sid)['track_id']
        with self.legal_simulation():
            result=self.final(sid,listened='on',cover_checked='on')
            self.assertEqual(result.status_code,303,result.get_data(as_text=True))
            self.assertEqual(self.row(sid)['status'],'approved')
            self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(tid,)).fetchone()[0],'approved')
            response=self.get(f'/media/cover/{tid}')
            self.assertEqual(response.status_code,200)
            response.data
            response.close()
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)
            self.assertEqual(self.final(sid,'reject',reason='Late').status_code,409)

    def test_final_rejection_requires_reason_and_preserves_assets_and_proofs(self):
        sid=self.deposited()
        before={str(p):p.read_bytes() for p in self.generated()}
        self.assertEqual(self.final(sid,'reject',reason=' ').status_code,400)
        self.assertEqual(self.final(sid,'reject',reason='Wrong cover').status_code,303)
        self.assertEqual(self.row(sid)['status'],'rejected')
        self.assertEqual(self.conn.execute('SELECT status FROM tracks').fetchone()[0],'rejected')
        self.assertEqual({str(p):p.read_bytes() for p in self.generated()},before)
        self.assertEqual(self.proof(sid)['declared_name'],'Declarant')

    def test_any_changed_media_prevents_approval(self):
        sid=self.deposited()
        proof=self.proof(sid)
        for field in ('audio_path','cover_original_path','cover_normalized_path'):
            path=Path(self.temp.name)/proof[field]
            original=path.read_bytes()
            path.chmod(0o600)
            path.write_bytes(b'replaced')
            with self.legal_simulation():
                self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)
            self.assertEqual(self.row(sid)['status'],'pending_audio_review')
            path.write_bytes(original)
            path.chmod(0o400)

    def test_missing_media_prevents_approval_but_can_be_rejected(self):
        sid=self.deposited()
        (Path(self.temp.name)/self.proof(sid)['cover_original_path']).unlink()
        with self.legal_simulation():
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)
        self.assertEqual(self.final(sid,'reject',reason='Missing image').status_code,303)

    def test_symlink_substitution_prevents_approval(self):
        sid=self.deposited()
        proof=self.proof(sid)
        path=Path(self.temp.name)/proof['audio_path']
        replacement=Path(self.temp.name)/'replacement.mp3'
        replacement.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(replacement)
        with self.legal_simulation():
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)

    def test_admin_demoted_or_unverified_during_final_transaction_is_denied(self):
        sid=self.deposited()
        from submission_audio import finalize_audio_review as real_finalize
        def demote(conn,*args,**kwargs):
            self.conn.execute("UPDATE users SET role='contributor' WHERE id=2")
            self.conn.commit()
            return real_finalize(conn,*args,**kwargs)
        with self.legal_simulation(),patch('submission_audio.finalize_audio_review',side_effect=demote):
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,403)
        self.assertEqual(self.row(sid)['status'],'pending_audio_review')

    def test_public_cover_detects_tampering_after_approval(self):
        sid=self.deposited()
        with self.legal_simulation():
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,303)
            proof=self.proof(sid)
            path=Path(self.temp.name)/proof['cover_normalized_path']
            path.chmod(0o600)
            path.write_bytes(b'changed')
            self.assertEqual(self.get(f'/media/cover/{self.row(sid)["track_id"]}').status_code,404)

    def test_publishing_second_file_failure_rolls_back_and_cleans(self):
        sid=self.accepted()
        real=StorageSession.publish
        def fail_second(session,prepared,root,name):
            if '-original' in name:raise OSError('simulated disk failure')
            return real(session,prepared,root,name)
        with patch.object(StorageSession,'publish',fail_second):
            self.assertEqual(self.upload(sid).status_code,503)
        self.assert_no_deposit(sid)

    def test_storage_directory_permission_failure_returns_503_without_deposit(self):
        sid=self.accepted()
        with patch('audio_storage.safe_directory',side_effect=PermissionError('simulated storage denial')):
            self.assertEqual(self.upload(sid).status_code,503)
        self.assert_no_deposit(sid)

    def test_failure_on_attachment_rolls_back_all_files_and_rows(self):
        sid=self.accepted()
        self.conn.execute("""CREATE TRIGGER fail_attach AFTER UPDATE OF track_id ON submissions
            WHEN NEW.track_id IS NOT NULL BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
        self.assertEqual(self.upload(sid).status_code,409)
        self.assert_no_deposit(sid)

    def test_final_second_update_failure_rolls_back_first_decision(self):
        sid=self.deposited()
        self.conn.execute("""CREATE TRIGGER fail_final BEFORE UPDATE OF status ON tracks
            WHEN NEW.status='approved' BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
        with self.legal_simulation():
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)
        self.assertEqual(self.row(sid)['status'],'pending_audio_review')
        self.assertIsNone(self.row(sid)['audio_reviewed_by'])
        self.assertEqual(self.conn.execute('SELECT status FROM tracks').fetchone()[0],'pending')

    def test_orphan_inventory_preserves_history_and_registered_assets(self):
        sid=self.deposited()
        orphan=Path(self.temp.name)/'digger-crash-original.png'
        orphan.write_bytes(b'orphan')
        legacy=Path(self.temp.name)/'legacy.jpg'
        legacy.write_bytes(b'history')
        self.assertEqual(orphan_candidates(self.conn,self.temp.name,self.temp.name),[orphan])
        self.assertTrue(legacy.exists())
        self.assertTrue(orphan.exists())

    def test_no_automatic_migration_and_legacy_upload_remains_closed(self):
        sid=self.accepted()
        with patch.object(db,'migrate_db',side_effect=AssertionError('No migration')):
            self.assertEqual(self.upload(sid).status_code,303)
            self.assertEqual(self.mutate('/tracks').status_code,410)
            self.session_user(2)
            for action in ('approve','reject','reintegrate','delete','refetch-cover'):
                self.assertEqual(self.mutate(f'/admin/tracks/{self.row(sid)["track_id"]}/{action}').status_code,409)

    def test_concurrent_deposits_have_one_winner_without_orphan_files(self):
        sid=self.accepted()
        barrier=threading.Barrier(2)
        from werkzeug.datastructures import FileStorage
        def attempt():
            conn=db.get_db(self.path)
            try:
                service=SubmissionAudioService(conn,self.temp.name,self.temp.name)
                barrier.wait(timeout=5)
                try:
                    service.deposit(1,sid,audio=FileStorage(io.BytesIO(mp3_bytes()),filename='track.mp3'),
                        cover=FileStorage(io.BytesIO(cover_bytes()),filename='cover.png'),declared_name='Owner',
                        declared_artist='Artist',declarant_capacity='rights_holder',music_accepted=True,visual_accepted=True,
                        document_version=rights.DRAFT_DOCUMENT.version,document_sha256=rights.DRAFT_DOCUMENT.sha256)
                    return 'ok'
                except SubmissionError as error:return error.code
            finally:conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(attempt) for _ in range(2)]
            self.assertCountEqual([j.result(timeout=15) for j in jobs],['ok','invalid_state'])
        self.assertEqual(len(self.generated()),3)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],1)

    def test_concurrent_final_decisions_are_atomic(self):
        sid=self.deposited()
        barrier=threading.Barrier(2)
        def attempt(approve):
            conn=db.get_db(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    SubmissionAudioService(conn,self.temp.name,self.temp.name).review(2,sid,approve=approve,
                        reason=None if approve else 'Refused',listened=True,cover_checked=True)
                    return 'ok'
                except SubmissionError as error:return error.code
            finally:conn.close()
        with self.legal_simulation(),concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs=[pool.submit(attempt,approve) for approve in (True,False)]
            self.assertCountEqual([j.result(timeout=15) for j in jobs],['ok','invalid_state'])
        state=self.row(sid)['status']
        self.assertIn(state,('approved','rejected'))
        self.assertEqual(self.conn.execute('SELECT status FROM tracks').fetchone()[0],state)

    def test_supplier_and_declarant_html_is_escaped(self):
        sid=self.accepted()
        self.assertEqual(self.upload(sid,declared_name='<script>alert(1)</script>').status_code,303)
        self.session_user(2)
        text=self.get(f'/admin/submissions/{sid}/audio').get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>',text)
        self.assertIn('&lt;script&gt;',text)

    def test_spotify_disabled_still_allows_owned_manual_files_without_remote_images(self):
        sid=self.draft(url=fixture.SPOTIFY)
        self.complete(sid)
        self.service.submit_for_review(1,sid)
        SubmissionReviewService(self.conn).accept(2,sid)
        self.assertEqual(self.upload(sid).status_code,303)
        self.http.assert_not_called()
        self.http_post.assert_not_called()
        self.assertTrue(self.proof(sid)['cover_normalized_path'].startswith('digger-'))

    def test_account_revoked_during_validation_rolls_back_without_track(self):
        sid=self.accepted()
        from submission_audio import prepare_cover as real_prepare
        def revoke(*args,**kwargs):
            self.conn.execute('UPDATE users SET is_verified=0 WHERE id=1')
            self.conn.commit()
            return real_prepare(*args,**kwargs)
        with patch('submission_audio.prepare_cover',side_effect=revoke):
            self.assertEqual(self.upload(sid).status_code,403)
        self.assert_no_deposit(sid)

    def test_new_filename_cannot_be_supplied_by_contributor(self):
        sid=self.accepted()
        self.assertEqual(self.upload(sid,audio=(io.BytesIO(mp3_bytes()),'../../arbitrary.mp3'),
            file_path='/tmp/arbitrary.mp3',status='approved',track_id='999').status_code,303)
        track=self.conn.execute('SELECT * FROM tracks').fetchone()
        self.assertTrue(track['file_path'].startswith('tracks/digger-'))
        self.assertNotIn('..',track['original_filename'])
        self.assertEqual(track['status'],'pending')

    def test_private_checks_and_media_hashing_run_outside_sql_write_transaction(self):
        sid=self.deposited()
        from submission_audio import checked_open as real_open
        def inspect(*args,**kwargs):
            self.assertFalse(self.conn.in_transaction)
            return real_open(*args,**kwargs)
        with self.legal_simulation(),patch('submission_audio.checked_open',side_effect=inspect) as opening:
            self.audio_service.review(2,sid,approve=True,listened=True,cover_checked=True)
        self.assertEqual(opening.call_count,3)

    def test_unverified_admin_cannot_pass_final_transaction(self):
        sid=self.deposited()
        from submission_audio import finalize_audio_review as real_finalize
        def unverify(conn,*args,**kwargs):
            self.conn.execute('UPDATE users SET is_verified=0 WHERE id=2')
            self.conn.commit()
            return real_finalize(conn,*args,**kwargs)
        with self.legal_simulation(),patch('submission_audio.finalize_audio_review',side_effect=unverify):
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,403)
        self.assertEqual(self.row(sid)['status'],'pending_audio_review')

    def test_change_between_hash_and_commit_prevents_final_approval(self):
        sid=self.deposited()
        from submission_audio import finalize_audio_review as real_finalize
        def change(conn,*args,**kwargs):
            path=Path(self.temp.name)/self.proof(sid)['cover_normalized_path']
            path.chmod(0o600)
            path.write_bytes(b'late change')
            return real_finalize(conn,*args,**kwargs)
        with self.legal_simulation(),patch('submission_audio.finalize_audio_review',side_effect=change):
            self.assertEqual(self.final(sid,listened='on',cover_checked='on').status_code,409)
        self.assertEqual(self.row(sid)['status'],'pending_audio_review')

    def test_commit_error_preserves_committed_files_or_cleans_rolled_back_files(self):
        from werkzeug.datastructures import FileStorage
        class CommitErrorConnection:
            def __init__(self,conn,committed):self.conn,self.committed=conn,committed
            def __getattr__(self,name):return getattr(self.conn,name)
            def __enter__(self):return self.conn.__enter__()
            def __exit__(self,*args):
                if args[0] is not None:return self.conn.__exit__(*args)
                if self.committed:self.conn.commit()
                else:self.conn.rollback()
                raise sqlite3.OperationalError('uncertain commit acknowledgement')
        sid=self.accepted()
        for committed in (False,True):
            service=SubmissionAudioService(CommitErrorConnection(self.conn,committed),self.temp.name,self.temp.name)
            with self.assertRaises(SubmissionError) as error:
                service.deposit(1,sid,audio=FileStorage(io.BytesIO(mp3_bytes()),filename='track.mp3'),
                    cover=FileStorage(io.BytesIO(cover_bytes()),filename='cover.png'),declared_name='Owner',
                    declared_artist='Artist',declarant_capacity='rights_holder',music_accepted=True,visual_accepted=True,
                    document_version=rights.DRAFT_DOCUMENT.version,document_sha256=rights.DRAFT_DOCUMENT.sha256)
            self.assertEqual(error.exception.code,'storage_error')
            if not committed:self.assert_no_deposit(sid)
            else:
                self.assertEqual(self.row(sid)['status'],'pending_audio_review')
                self.assertEqual(len(self.generated()),3)

    def test_rejected_deposit_remains_private_and_cannot_be_replaced(self):
        sid=self.deposited()
        self.assertEqual(self.final(sid,'reject',reason='Wrong artwork').status_code,303)
        tid=self.row(sid)['track_id']
        self.assertEqual(self.get(f'/media/cover/{tid}').status_code,404)
        self.session_user(1)
        self.assertEqual(self.upload(sid).status_code,409)
        self.assertNotIn('type="file"',self.get(f'/submissions/{sid}/audio').get_data(as_text=True))

    def test_private_audio_supports_byte_ranges_for_listening_and_seeking(self):
        sid=self.deposited()
        response=self.get(f'/submissions/{sid}/audio/media/audio',headers={'Range':'bytes=0-9'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.data,mp3_bytes()[:10])
        self.assertIn('bytes 0-9/',response.headers['Content-Range'])
        response.close()

    def historical_linked(self,approved):
        root=Path(__file__).resolve().parents[1]
        directory=Path(self.temp.name)/'old-migrations'
        directory.mkdir()
        for filename in ('001_submissions.sql','002_submission_metadata.sql'):
            shutil.copyfile(root/'migrations'/filename,directory/filename)
        path=Path(self.temp.name)/'older.sqlite'
        conn=db.get_db(path)
        self.addCleanup(conn.close)
        conn.executescript((root/'schema.sql').read_text())
        db.migrate_db(path,migrations_dir=directory)
        conn.executescript("""INSERT INTO users(id,email,role,is_verified)
            VALUES (1,'old@example.test','contributor',1),(2,'admin@example.test','admin',1);
            INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url)
            VALUES (1,'youtube','dQw4w9WgXcQ','https://youtu.be/dQw4w9WgXcQ','https://youtu.be/dQw4w9WgXcQ');
            UPDATE submissions SET status='pending_review',title='Old',artist='Artist',genre_id=1;
            UPDATE submissions SET status='approved_awaiting_audio',reviewed_by=2,reviewed_at=CURRENT_TIMESTAMP;
            INSERT INTO tracks(uploader_id,file_path,title,genre_id,meta_path)
            VALUES (1,'tracks/historical.mp3','Old',1,'historical.jpg');
            UPDATE submissions SET status='pending_audio_review',track_id=1;""")
        if approved:db.finalize_audio_review(conn,1,2,approve=True)
        db.migrate_db(path)
        tracks=Path(self.temp.name)/'tracks'
        tracks.mkdir(exist_ok=True)
        (tracks/'historical.mp3').write_bytes(b'existing historical MP3')
        (Path(self.temp.name)/'historical.jpg').write_bytes(cover_bytes('JPEG'))
        return path,conn

    def test_already_approved_linked_historical_cover_is_preserved_without_fabricated_rights(self):
        path,conn=self.historical_linked(True)
        before=tuple(conn.execute('SELECT * FROM tracks').fetchone())
        with patch.object(self.module,'get_db',side_effect=lambda:db.get_db(path)):
            response=self.get('/media/cover/1')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.data,cover_bytes('JPEG'))
            response.close()
            self.session_user(2)
            response=self.get('/media/1')
            self.assertEqual(response.status_code,200)
            response.data
            response.close()
        self.assertEqual(tuple(conn.execute('SELECT * FROM tracks').fetchone()),before)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_authorizations').fetchone()[0],0)

    def test_old_pending_audio_without_proof_can_be_refused_but_never_approved(self):
        path,conn=self.historical_linked(False)
        with patch.object(self.module,'get_db',side_effect=lambda:db.get_db(path)):
            self.session_user(2)
            page=self.get('/admin/submissions/1/audio')
            self.assertEqual(page.status_code,200)
            self.assertIn('sans preuve complète',page.get_data(as_text=True))
            self.assertEqual(self.get('/media/cover/1').status_code,404)
            self.assertEqual(self.final(1,listened='on',cover_checked='on').status_code,409)
            self.assertEqual(self.final(1,'reject',reason='Missing authorizations').status_code,303)
        self.assertEqual(conn.execute('SELECT status FROM submissions').fetchone()[0],'rejected')
        self.assertEqual(conn.execute('SELECT status FROM tracks').fetchone()[0],'rejected')
        self.assertTrue((Path(self.temp.name)/'tracks/historical.mp3').exists())
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_authorizations').fetchone()[0],0)
