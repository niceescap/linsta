"""DEV-only editorial views. No engine control or database writes."""
from pathlib import Path
from flask import Blueprint, abort, current_app, g, render_template, redirect, url_for
from preview_radio_web import track, PreviewError


def install(application, login_required):
    bp = Blueprint('editorial', __name__)

    def page(section='radio', item=None):
        titles = {'radio': 'Radio LIVE', 'decouvrir': 'Découvrir', 'artistes': 'Artistes',
                  'a-propos': 'À propos', 'connexion': 'Connexion', 'identification': 'Identification',
                  'compte': 'Mon espace', 'auditeur': 'Espace auditeur', 'morceau': item['title'] if item else 'Morceau'}
        return render_template('editorial/shell.html', section=section, page_title=titles[section],
                               item=item, user=g.current_user)

    @bp.get('/decouvrir')
    def discover():
        return page('decouvrir')

    @bp.get('/artistes')
    def artists():
        return page('artistes')

    @bp.get('/a-propos')
    def about():
        return page('a-propos')

    @bp.get('/morceaux/<int:track_id>')
    @login_required
    def detail(track_id):
        try:
            item = track(Path(current_app.config['PREVIEW_RADIO_ROOT']), track_id)
        except PreviewError:
            abort(404)
        return page('morceau', item)


    profiles = {'auditeur': 'Auditeur', 'artiste': 'Artiste / Contributeur',
                'administrateur': 'Administrateur'}

    @bp.get('/connexion')
    def connection():
        return page('connexion')

    @bp.get('/connexion/<profile>')
    def identify(profile):
        if profile not in profiles:
            abort(404)
        if g.current_user is not None:
            return redirect(url_for('.account'))
        return render_template('editorial/shell.html', section='identification',
                               page_title='Connexion — ' + profiles[profile],
                               profile_label=profiles[profile], user=None, item=None)

    @bp.get('/mon-espace')
    @login_required
    def account():
        return page('compte')

    @bp.get('/espace-auditeur')
    @login_required
    def listener():
        return page('auditeur')

    def local_identify():
        return redirect(url_for('editorial.connection'))

    application.register_blueprint(bp)
    if 'local_login' in application.view_functions:
        application.view_functions['local_login'] = local_identify
    application.view_functions['index'] = page
