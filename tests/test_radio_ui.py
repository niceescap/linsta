"""Merged UI with real Flask, isolated SQLite and mocked OAuth/providers."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

import test_flask_security as security


class Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {'attrs': attrs, 'fields': []}
            self.forms.append(self.current)
        elif tag == 'input' and self.current is not None:
            self.current['fields'].append(attrs)
        elif tag == 'img':
            self.images.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None


class RadioUITests(unittest.TestCase):
    setUpClass = classmethod(security.FlaskSecurityTests.setUpClass.__func__)
    setUp = security.FlaskSecurityTests.setUp
    get = security.FlaskSecurityTests.get
    session_user = security.FlaskSecurityTests.session_user

    def test_public_digger_branding_assets_and_google_login(self):
        response = self.get('/')
        html = response.get_data(as_text=True)
        self.assertIn('<title>Digger Radio', html)
        self.assertIn('href="/login/google"', html)
        self.assertNotIn('ALIVE', html)
        for name in ('digger-icon.png', 'digger-logo-horizontal.png', 'digger-og.jpg',
                     'css/radio.css', 'js/radio.js'):
            asset = self.get('/static/' + name)
            self.assertEqual(asset.status_code, 200, name)
            asset.close()
        self.assertIn('playsinline', html)
        self.assertIn('id="player-status"', html)

    def test_contributor_has_url_first_link_and_protected_logout(self):
        self.session_user(1)
        html = self.get('/').get_data(as_text=True)
        self.assertIn('href="/submissions"', html)
        self.assertNotIn('action="/tracks"', html)
        parser = Forms()
        parser.feed(html)
        logout = next(f for f in parser.forms if f['attrs'].get('action') == '/logout-form')
        self.assertTrue(any(f.get('name') == 'csrf_token' and f.get('value') for f in logout['fields']))
        self.assertFalse(any(f.get('type') == 'file' for form in parser.forms for f in form['fields']))

    def test_admin_retains_review_navigation_csrf_and_valid_cover_markup(self):
        self.session_user(2)
        with self.conn:
            self.conn.execute("""INSERT INTO tracks(uploader_id,file_path,title,genre_id,status)
                VALUES (1,'tracks/legacy.mp3','Legacy',1,'pending')""")
        html = self.get('/admin').get_data(as_text=True)
        self.assertIn('Admin — Digger Radio', html)
        self.assertIn('/admin/submissions', html)
        parser = Forms()
        parser.feed(html)
        self.assertTrue(any(image.get('src', '').startswith('/media/cover/') for image in parser.images))
        for form in parser.forms:
            if form['attrs'].get('method') == 'post':
                self.assertTrue(any(f.get('name') == 'csrf_token' for f in form['fields']))
