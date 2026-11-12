"""Editorial routes use temporary catalogues and a fake controller only."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from test_preview_radio_web import PreviewRoutesTests
from editorial_web import install


class EditorialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PreviewRoutesTests.setUpClass.__func__(cls)
        install(cls.app, cls.module.login_required)

    setUp = PreviewRoutesTests.setUp
    get = PreviewRoutesTests.get
    session_user = PreviewRoutesTests.session_user
    publish = PreviewRoutesTests.publish
    post = PreviewRoutesTests.post
    token = PreviewRoutesTests.token

    def test_editorial_pages_have_one_audio_and_no_public_stream(self):
        for route in ('/', '/decouvrir', '/artistes', '/a-propos'):
            response = self.get(route)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertEqual(html.count('<audio '), 1)
            self.assertIn('id="content"', html)
            self.assertIn('data-enabled="false"', html)
            self.assertNotIn('24/7', html)
            self.assertNotIn('data-stream-url="/hls/', html)
            self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.get('/morceaux/1').status_code, 401)
        self.assertEqual(self.controller.actions, [])

    def test_authenticated_detail_is_read_only_and_has_no_on_demand_audio(self):
        self.session_user(1)
        html = self.get('/morceaux/1').get_data(as_text=True)
        self.assertIn('Track 1', html)
        self.assertIn('data-enabled="true"', html)
        self.assertEqual(html.count('<audio '), 1)
        self.assertNotIn('/tracks/1/audio', html)
        self.assertEqual(self.get('/morceaux/9999').status_code, 404)
        self.assertEqual(self.controller.actions, [])

    def test_home_uses_original_hero_and_honest_empty_history(self):
        html = self.get('/').get_data(as_text=True)
        self.assertIn('L’historique d’antenne n’est pas encore disponible.', html)
        self.assertNotIn('accueil-desktop-mobile.png', html)
        response = self.get('/static/editorial/editorial.css')
        css = response.get_data(as_text=True)
        response.close()
        self.assertIn('digger-hero.png', css)
        self.assertIn('prefers-reduced-motion', css)
        self.assertIn('safe-area-inset-bottom', css)
        for name in ('player.js','navigation.js','digger-hero.png'):
            response = self.get('/static/editorial/'+name)
            self.assertEqual(response.status_code,200)
            response.close()

    def test_optional_radio_diagnostic_is_only_rendered_for_logged_in_user(self):
        self.assertNotIn('id="radio-diagnostics"', self.get('/?radioDiagnostics=1').get_data(as_text=True))
        self.session_user(1)
        self.assertIn('Exporter le diagnostic', self.get('/?radioDiagnostics=1').get_data(as_text=True))
        self.assertNotIn('id="radio-diagnostics"', self.get('/').get_data(as_text=True))

    def test_profile_selection_never_grants_a_role(self):
        for profile in ('auditeur', 'artiste', 'administrateur'):
            response = self.get('/connexion/' + profile)
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn('action="/login"', html)
            self.assertIn('name="csrf_token"', html)
            self.assertNotIn('name="role"', html)
            with self.client.session_transaction(base_url=self.base) as session:
                self.assertNotIn('user_id', session)
                self.assertNotIn('role', session)
        html = self.get('/').get_data(as_text=True)
        self.assertEqual(html.count('>Connexion</a>'), 1)
        self.assertEqual(self.get('/mon-espace').status_code, 401)
        self.assertEqual(self.get('/espace-auditeur').status_code, 401)
        self.assertEqual(self.get('/connexion/inconnu').status_code, 404)
        response = self.get('/login/local')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/connexion')

    def test_real_login_uses_database_role_even_after_admin_profile_selection(self):
        self.get('/connexion/administrateur')
        token = self.token()
        self.assertEqual(self.post('/login', json={'email':'user1@example.test',
            'password':'test-password', 'role':'admin'}, headers={'X-CSRF-Token':token}).status_code, 200)
        html = self.get('/mon-espace').get_data(as_text=True)
        self.assertIn('Espace artiste / contributeur', html)
        self.assertIn('href="/submissions"', html)
        self.assertNotIn('href="/admin"', html)
        self.assertEqual(self.get('/admin').status_code, 403)
        self.assertEqual(self.get('/admin/preview-radio').status_code, 403)
        self.assertEqual(self.get('/connexion/administrateur').status_code, 302)
        self.assertIn('En construction', self.get('/espace-auditeur').get_data(as_text=True))

    def test_admin_dashboard_logout_and_role_revocation(self):
        token = self.token()
        self.assertEqual(self.post('/login', json={'email':'user2@example.test',
            'password':'test-password'}, headers={'X-CSRF-Token':token}).status_code, 200)
        self.assertIn('href="/admin"', self.get('/mon-espace').get_data(as_text=True))
        self.assertEqual(self.get('/admin').status_code, 200)
        self.assertEqual(self.get('/admin/preview-radio').status_code, 200)
        with self.conn:
            self.conn.execute("UPDATE users SET role='contributor' WHERE id=2")
        self.assertNotIn('href="/admin"', self.get('/mon-espace').get_data(as_text=True))
        self.assertEqual(self.get('/admin').status_code, 403)
        self.assertEqual(self.post('/logout-form').status_code, 400)
        self.assertEqual(self.post('/logout-form',data={'csrf_token':self.token()}).status_code,302)
        self.assertEqual(self.get('/mon-espace').status_code,401)
        self.assertEqual(self.get('/preview-radio/hls/stream.m3u8').status_code,401)


if __name__ == '__main__':
    unittest.main()
