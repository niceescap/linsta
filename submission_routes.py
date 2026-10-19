"""Contributor HTML routes. Authorization/services own the business decisions.

Registered through a factory to keep the existing Flask app and test connection
injection. Every connection closes on success and errors. Global CSRF from
web_security protects every POST. There are no admin or audio routes here.
"""
from contextlib import contextmanager
import re
from urllib.parse import urlsplit

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from metadata import MetadataURLValidationError, normalize_source_url
from submissions import SubmissionError, SubmissionService


STATUS_LABELS = {
    'draft': 'Brouillon', 'pending_review': 'En attente de validation',
    'approved_awaiting_audio': 'Acceptée — MP3 attendu',
    'pending_audio_review': 'Audio en attente de validation',
    'approved': 'Audio validé', 'rejected': 'Refusée',
}
METADATA_LABELS = {'pending': 'En attente de récupération', 'available': 'Métadonnées disponibles',
                   'partial': 'Métadonnées partielles', 'failed': 'Récupération indisponible',
                   'superseded': 'Tentative remplacée'}
ERROR_MESSAGES = {
    'disabled': 'La récupération Spotify est désactivée. Vous pouvez compléter les champs manuellement.',
    'invalid_configuration': 'La récupération automatique est indisponible. La saisie manuelle reste possible.',
    'missing_credentials': 'La récupération automatique est indisponible. La saisie manuelle reste possible.',
    'missing_api_key': 'La récupération automatique est indisponible. La saisie manuelle reste possible.',
    'access_denied': 'La source ne permet pas la récupération des métadonnées.',
    'not_found': 'Le contenu est introuvable ou inaccessible sur la plateforme.',
    'quota_exceeded': 'La limite de récupération est atteinte. Réessayez plus tard ou complétez les champs.',
    'rate_limited': 'La plateforme reçoit trop de requêtes. Réessayez plus tard.',
    'timeout': 'La plateforme ne répond pas à temps. Vous pouvez réessayer.',
    'network_error': 'La plateforme est momentanément inaccessible. Vous pouvez réessayer.',
    'service_unavailable': 'La plateforme est momentanément indisponible.',
    'http_error': 'La récupération a échoué. La saisie manuelle reste possible.',
    'invalid_response': 'Les métadonnées reçues sont inutilisables. Vous pouvez compléter les champs.',
    'source_relinked': 'Spotify propose une autre version de ce morceau. Vérifiez les informations manuellement.',
    'token_expired': 'La récupération a échoué. Vous pouvez réessayer.',
}


def _error_status(error):
    if isinstance(error, MetadataURLValidationError):
        return 400
    return {'unauthorized': 403, 'not_found': 404, 'page_not_found': 404,
            'duplicate_source': 409, 'invalid_state': 409}.get(error.code, 400)


def _field(name):
    values = request.form.getlist(name)
    if len(values) > 1:
        raise SubmissionError('invalid_form', 'Un champ a été envoyé plusieurs fois.')
    return values[0] if values else None


def _safe_artwork(value):
    # Display a link only; no server or browser image fetch is initiated.
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme == 'https' and parts.hostname and parts.username is None
                and parts.password is None and parts.port is None and '\\' not in value
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
            return value
    except ValueError:
        pass
    return None


def create_submission_blueprint(connection_factory, login_required):
    bp = Blueprint('contributions', __name__)

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
                'metadata_error_messages': ERROR_MESSAGES}

    @bp.after_request
    def private_response(response):
        response.headers['Cache-Control'] = 'no-store'
        response.vary.add('Cookie')
        return response

    @bp.errorhandler(SubmissionError)
    @bp.errorhandler(MetadataURLValidationError)
    def business_error(error):
        return render_template('submissions.html', page_data=None, error=str(error), source_url=''), _error_status(error)

    def detail(conn, service, submission_id, *, error=None, form_values=None):
        proposal = service.get_submission(g.current_user['id'], submission_id)
        genres = conn.execute('SELECT id,label FROM genres ORDER BY sort').fetchall()
        try:
            source_link = normalize_source_url(proposal['source_url']).source_url
        except MetadataURLValidationError:
            source_link = None
        return render_template('submission_detail.html', proposal=proposal, genres=genres,
            history=service.get_history(g.current_user['id'], submission_id, limit=10),
            source_link=source_link, artwork_link=_safe_artwork(proposal['artwork_url']),
            error=error, form_values=form_values)

    @bp.get('/submissions')
    @login_required
    def dashboard():
        page = request.args.get('page', '1')
        if not re.fullmatch(r'[0-9]{1,10}', page):
            raise SubmissionError('invalid_page', 'Numéro de page invalide.')
        with connection() as conn:
            data = SubmissionService(conn).list_submissions(g.current_user['id'], page=int(page))
            return render_template('submissions.html', page_data=data, source_url='', error=None)

    @bp.post('/submissions')
    @login_required
    def create():
        with connection() as conn:
            service = SubmissionService(conn)
            source_url = _field('source_url')
            try:
                if request.files:
                    raise SubmissionError('invalid_form', 'Cette étape accepte uniquement un lien, sans fichier audio.')
                proposal = service.create_draft(g.current_user['id'], source_url)
            except (SubmissionError, MetadataURLValidationError) as error:
                return render_template('submissions.html',
                    page_data=service.list_submissions(g.current_user['id']),
                    source_url=source_url or '', error=str(error)), _error_status(error)
        flash('Brouillon créé. Vérifiez les informations et complétez les champs nécessaires.', 'success')
        return redirect(url_for('.show', submission_id=proposal['id']), code=303)

    @bp.get('/submissions/<int:submission_id>')
    @login_required
    def show(submission_id):
        with connection() as conn:
            return detail(conn, SubmissionService(conn), submission_id)

    @bp.post('/submissions/<int:submission_id>/editorial')
    @login_required
    def editorial(submission_id):
        with connection() as conn:
            service = SubmissionService(conn)
            service.get_submission(g.current_user['id'], submission_id)  # ownership before parsing
            try:
                fields = {key: _field(key) for key in ('title', 'artist', 'genre_id') if key in request.form}
                if 'genre_id' in fields:
                    genre = fields['genre_id']
                    if genre:
                        if not re.fullmatch(r'[0-9]{1,19}', genre):
                            raise SubmissionError('invalid_genre', 'Genre absent du référentiel.')
                        fields['genre_id'] = int(genre)
                    else:
                        fields['genre_id'] = None
                service.update_editorial(g.current_user['id'], submission_id, **fields)
            except SubmissionError as error:
                return detail(conn, service, submission_id, error=str(error),
                              form_values=request.form), _error_status(error)
        flash('Informations enregistrées.', 'success')
        return redirect(url_for('.show', submission_id=submission_id), code=303)

    @bp.post('/submissions/<int:submission_id>/refresh-metadata')
    @login_required
    def refresh(submission_id):
        with connection() as conn:
            SubmissionService(conn).refresh_metadata(g.current_user['id'], submission_id)
        flash('La tentative de récupération est terminée. Consultez son résultat ci-dessous.', 'success')
        return redirect(url_for('.show', submission_id=submission_id), code=303)

    @bp.post('/submissions/<int:submission_id>/submit')
    @login_required
    def submit(submission_id):
        with connection() as conn:
            service = SubmissionService(conn)
            try:
                service.submit_for_review(g.current_user['id'], submission_id)
            except SubmissionError as error:
                # Ownership failures are handled without rendering another user's detail.
                if error.code in ('not_found', 'unauthorized'):
                    raise
                return detail(conn, service, submission_id, error=str(error)), _error_status(error)
        flash('Votre proposition a été envoyée pour validation.', 'success')
        return redirect(url_for('.show', submission_id=submission_id), code=303)

    return bp
