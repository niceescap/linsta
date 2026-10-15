"""Administrative URL review routes; global security supplies CSRF/account checks."""
from contextlib import contextmanager
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from metadata import MetadataURLValidationError, normalize_source_url
from submission_routes import STATUS_LABELS, METADATA_LABELS, ERROR_MESSAGES, _field, _safe_artwork, _error_status
from submission_review import SubmissionReviewService, STATUSES, PLATFORMS
from submissions import SubmissionError


def create_submission_review_blueprint(connection_factory, admin_required):
    bp = Blueprint('submission_review', __name__)

    @contextmanager
    def connection():
        conn = connection_factory()
        try:
            yield conn
        finally:
            conn.close()

    @bp.context_processor
    def labels():
        return {'status_labels': STATUS_LABELS, 'metadata_labels': METADATA_LABELS,
                'metadata_error_messages': ERROR_MESSAGES, 'statuses': STATUSES, 'platforms': PLATFORMS}

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'no-store'
        response.vary.add('Cookie')
        return response

    @bp.errorhandler(SubmissionError)
    def business_error(error):
        return render_template('admin_submissions.html', error=str(error), page_data=None), _error_status(error)

    def detail(conn, service, submission_id, *, error=None, form_values=None):
        proposal = service.get_submission(g.current_user['id'], submission_id)
        try:
            source_link = normalize_source_url(proposal['source_url']).canonical_url
        except MetadataURLValidationError:
            source_link = None
        return render_template('admin_submission_detail.html', proposal=proposal,
            genres=conn.execute('SELECT id,label FROM genres ORDER BY sort').fetchall(),
            source_link=source_link, artwork_link=_safe_artwork(proposal['artwork_url']),
            error=error, form_values=form_values)

    @bp.get('/admin/submissions')
    @admin_required
    def queue():
        page = request.args.get('page', '1')
        if not re.fullmatch(r'[0-9]{1,10}', page):
            raise SubmissionError('invalid_page', 'Pagination invalide.')
        status = request.args.get('status', 'pending_review') or None
        platform = request.args.get('platform') or None
        with connection() as conn:
            data = SubmissionReviewService(conn).list_submissions(g.current_user['id'],
                status=status, platform=platform, page=int(page))
            return render_template('admin_submissions.html', page_data=data,
                status_filter=status, platform_filter=platform, error=None)

    @bp.get('/admin/submissions/<int:submission_id>')
    @admin_required
    def show(submission_id):
        with connection() as conn:
            return detail(conn, SubmissionReviewService(conn), submission_id)

    def mutate(submission_id, action):
        with connection() as conn:
            service = SubmissionReviewService(conn)
            service.get_submission(g.current_user['id'], submission_id)
            try:
                if request.files:
                    raise SubmissionError('invalid_form', 'Aucun fichier audio n’est accepté à cette étape.')
                if action == 'editorial':
                    fields = {key: _field(key) for key in ('title', 'artist', 'genre_id') if key in request.form}
                    if 'genre_id' in fields:
                        value = fields['genre_id']
                        if value and not re.fullmatch(r'[0-9]{1,19}', value):
                            raise SubmissionError('invalid_genre', 'Genre absent du référentiel.')
                        fields['genre_id'] = int(value) if value else None
                    service.update_editorial(g.current_user['id'], submission_id, **fields)
                elif action == 'accept':
                    service.accept(g.current_user['id'], submission_id)
                else:
                    service.reject(g.current_user['id'], submission_id, reason=_field('reason'))
            except SubmissionError as error:
                if error.code in ('unauthorized', 'not_found'):
                    raise
                return detail(conn, service, submission_id, error=str(error),
                              form_values=request.form), _error_status(error)
        flash('Informations enregistrées.' if action == 'editorial' else 'Décision enregistrée.', 'success')
        return redirect(url_for('.show', submission_id=submission_id), code=303)

    @bp.post('/admin/submissions/<int:submission_id>/editorial')
    @admin_required
    def editorial(submission_id):
        return mutate(submission_id, 'editorial')

    @bp.post('/admin/submissions/<int:submission_id>/accept')
    @admin_required
    def accept(submission_id):
        return mutate(submission_id, 'accept')

    @bp.post('/admin/submissions/<int:submission_id>/reject')
    @admin_required
    def reject(submission_id):
        return mutate(submission_id, 'reject')

    return bp
