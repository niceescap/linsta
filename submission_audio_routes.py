"""Conditional contributor upload, private playback and explicit final admin review."""
from contextlib import contextmanager
import os

from flask import (Blueprint, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_file, url_for)

from audio_storage import StorageError
from rights import DRAFT_DOCUMENT, RightsError, document_is_production_ready
from submission_audio import SubmissionAudioService
from submission_routes import _field, STATUS_LABELS
from submissions import SubmissionError


def error_status(error):
    return {'unauthorized':403,'not_found':404,'invalid_state':409,'missing_evidence':409,
            'document_not_validated':409,'media_integrity':409,'media_missing':409,
            'file_too_large':413,'storage_error':503}.get(error.code,400)


def audio_preflight(connection_factory):
    """Run after account loading and BEFORE CSRF reads multipart request.form."""
    if request.endpoint!='submission_audio.upload' or request.method!='POST': return None
    if g.current_user is None:
        return jsonify(error='Un compte vérifié est requis.'),403 if g.account_unverified else 401
    conn=connection_factory()
    try:
        SubmissionAudioService(conn,'.','.').get(g.current_user['id'],request.view_args['submission_id'],awaiting=True)
    except SubmissionError as error:
        return jsonify(error=str(error),code=error.code),error_status(error)
    finally:
        conn.close()


def create_audio_blueprint(connection_factory,roots_factory,login_required,admin_required):
    bp=Blueprint('submission_audio',__name__)

    @contextmanager
    def service():
        conn=connection_factory()
        try:
            storage,meta=roots_factory()
            yield SubmissionAudioService(conn,storage,meta,current_app.config['MEDIA_LIMITS'])
        finally: conn.close()

    @bp.after_request
    def private(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.vary.add('Cookie')
        return response

    @bp.errorhandler(SubmissionError)
    @bp.errorhandler(StorageError)
    @bp.errorhandler(RightsError)
    def business_error(error):
        template='submission_audio_review.html' if request.path.startswith('/admin/') else 'submission_audio.html'
        return render_template(template,proposal=None,proof=None,document=DRAFT_DOCUMENT,
            status_labels=STATUS_LABELS,error=str(error)),error_status(error)

    def owned_page(audio_service,sid):
        proposal=audio_service.get(g.current_user['id'],sid)
        if proposal['status']!='approved_awaiting_audio' and proposal['track_id'] is None:
            raise SubmissionError('invalid_state','Le dépôt est ouvert uniquement après acceptation de la proposition URL.')
        proof=audio_service._evidence(sid) if proposal['track_id'] else None
        return render_template('submission_audio.html',proposal=proposal,proof=proof,
            document=DRAFT_DOCUMENT,status_labels=STATUS_LABELS,error=None,
            limits=current_app.config['MEDIA_LIMITS'])

    @bp.get('/submissions/<int:submission_id>/audio')
    @login_required
    def show(submission_id):
        with service() as audio_service: return owned_page(audio_service,submission_id)

    @bp.post('/submissions/<int:submission_id>/audio')
    @login_required
    def upload(submission_id):
        with service() as audio_service:
            audio_service.get(g.current_user['id'],submission_id,awaiting=True)
            if set(request.files)!={'audio','cover'} or any(len(request.files.getlist(k))!=1 for k in request.files):
                raise SubmissionError('invalid_file','Un seul MP3 et une seule cover sont requis.')
            audio_service.deposit(g.current_user['id'],submission_id,
                audio=request.files['audio'],cover=request.files['cover'],
                declared_name=_field('declared_name'),declared_artist=_field('declared_artist'),
                declarant_capacity=_field('declarant_capacity'),music_accepted=_field('music_accepted')=='on',
                visual_accepted=_field('visual_accepted')=='on',document_version=_field('document_version'),
                document_sha256=_field('document_sha256'))
        flash('MP3, cover et déclarations enregistrés. Une seconde validation administrative est obligatoire.','success')
        return redirect(url_for('.show',submission_id=submission_id),code=303)

    @bp.get('/submissions/<int:submission_id>/audio/conditions')
    @login_required
    def conditions(submission_id):
        # Informational only: no authorization registry, acceptance or state writes.
        with service() as audio_service:
            audio_service.get(g.current_user['id'],submission_id)
        return render_template('submission_conditions_v03.html', submission_id=submission_id)

    @bp.get('/admin/submissions/<int:submission_id>/audio')
    @admin_required
    def review_page(submission_id):
        with service() as audio_service:
            proposal=audio_service.get(g.current_user['id'],submission_id,admin=True)
            try:
                proof=audio_service._evidence(submission_id)
            except SubmissionError as error:
                if error.code!='missing_evidence' or proposal['track_id'] is None: raise
                proof=None
            ready=bool(proof and document_is_production_ready(proof['document_version'],proof['document_text'],proof['document_sha256']))
            return render_template('submission_audio_review.html',proposal=proposal,proof=proof,
                production_ready=ready,status_labels=STATUS_LABELS,error=None)

    @bp.post('/admin/submissions/<int:submission_id>/audio/approve')
    @admin_required
    def approve(submission_id):
        with service() as audio_service:
            audio_service.review(g.current_user['id'],submission_id,approve=True,
                listened=_field('listened')=='on',cover_checked=_field('cover_checked')=='on')
        flash('MP3 et cover approuvés explicitement. Le morceau est éligible à la radio.','success')
        return redirect(url_for('.review_page',submission_id=submission_id),code=303)

    @bp.post('/admin/submissions/<int:submission_id>/audio/reject')
    @admin_required
    def reject(submission_id):
        with service() as audio_service:
            audio_service.review(g.current_user['id'],submission_id,approve=False,reason=_field('reason'))
        flash('Dépôt refusé. Le motif et les preuves sont conservés.','success')
        return redirect(url_for('.review_page',submission_id=submission_id),code=303)

    def media(submission_id,kind,admin):
        with service() as audio_service:
            stream,mime=audio_service.open_private(g.current_user['id'],submission_id,kind,admin=admin)
        try:
            response=send_file(stream,mimetype=mime,download_name='preview.mp3' if kind=='audio' else 'cover',
                               conditional=False,etag=False,max_age=0)
            response.content_length=os.fstat(stream.fileno()).st_size
            response.make_conditional(request.environ,accept_ranges=True,complete_length=response.content_length)
            response.call_on_close(stream.close)
            return response
        except BaseException:
            stream.close()
            raise

    @bp.get('/admin/submissions/<int:submission_id>/audio/media/<kind>')
    @admin_required
    def admin_media(submission_id,kind): return media(submission_id,kind,True)

    @bp.get('/submissions/<int:submission_id>/audio/media/<kind>')
    @login_required
    def owned_media(submission_id,kind): return media(submission_id,kind,False)

    return bp
