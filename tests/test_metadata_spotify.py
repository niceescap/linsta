import copy
import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import requests

from metadata import (MetadataRetrievalError, MetadataURLValidationError,
                      SpotifyMetadataClient, normalize_source_url)
from metadata.spotify import TOKEN_URL, TRACK_URL, TIMEOUT


TRACK = '4uLU6hMCjMI75M1A2tKUQC'
URL = f'https://open.spotify.com/track/{TRACK}'
# Deliberately fake fixture values, never operational credentials.
CLIENT_ID, SECRET, TOKEN = 'fake-client', 'fake-secret', 'fake-token'
PAYLOAD = {'id': TRACK, 'type': 'track', 'name': 'Titre',
           'artists': [{'name': 'Artiste A'}, {'name': 'Artiste B'}], 'duration_ms': 123456,
           'album': {'name': 'Album', 'release_date': '2020-02-29', 'release_date_precision': 'day',
                     'images': [{'url': 'https://i.scdn.co/image/fake-cover'}]}}


def response(payload, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = payload
    return result


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'SPOTIFY_METADATA_MODE': 'web_api',
            'SPOTIFY_CLIENT_ID': CLIENT_ID, 'SPOTIFY_CLIENT_SECRET': SECRET, 'SPOTIFY_MARKET': 'fr'})
        environment.start()
        self.addCleanup(environment.stop)
        socket = patch('socket.socket', side_effect=AssertionError('Network forbidden'))
        socket.start()
        self.addCleanup(socket.stop)
        self.token_response = response({'access_token': TOKEN, 'token_type': 'Bearer', 'expires_in': 3600})
        self.track_response = response(copy.deepcopy(PAYLOAD))
        post = patch('metadata.spotify.requests.post', return_value=self.token_response)
        get = patch('metadata.spotify.requests.get', return_value=self.track_response)
        self.post, self.get = post.start(), get.start()
        self.addCleanup(post.stop)
        self.addCleanup(get.stop)
        timer = patch('metadata.spotify.time.monotonic', return_value=100)
        self.clock = timer.start()
        self.addCleanup(timer.stop)
        self.client = SpotifyMetadataClient()

    def assert_error(self, code, retryable=False):
        with self.assertRaises(MetadataRetrievalError) as caught:
            self.client.fetch(URL)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.retryable, retryable)
        for private in (CLIENT_ID, SECRET, TOKEN):
            self.assertNotIn(private, str(caught.exception))
        return caught.exception

    def test_complete_metadata_and_official_endpoints(self):
        result = self.client.fetch(normalize_source_url(URL))
        self.assertEqual(result.source.source_id, TRACK)
        self.assertEqual(result.title, 'Titre')
        self.assertEqual(result.artist, 'Artiste A, Artiste B')
        self.assertEqual(result.duration_seconds, 123.456)
        self.assertEqual(result.album, 'Album')
        self.assertEqual(result.publication_date, '2020-02-29')
        self.assertEqual(result.publication_date_precision, 'day')
        self.assertEqual(result.artwork_url, 'https://i.scdn.co/image/fake-cover')
        self.assertIsNone(result.channel)
        self.assertIsNone(result.youtube_view_count)
        self.post.assert_called_once_with(TOKEN_URL, auth=(CLIENT_ID, SECRET),
            data={'grant_type': 'client_credentials'}, headers={'Accept': 'application/json'},
            timeout=TIMEOUT, allow_redirects=False)
        self.get.assert_called_once_with(TRACK_URL + TRACK, params={'market': 'FR'},
            headers={'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/json'},
            timeout=TIMEOUT, allow_redirects=False)
        self.token_response.close.assert_called_once()
        self.track_response.close.assert_called_once()

    def test_disabled_by_default_even_with_credentials(self):
        with patch.dict(os.environ):
            os.environ.pop('SPOTIFY_METADATA_MODE')
            self.assert_error('disabled')
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_disabled_clears_cached_token(self):
        self.client.fetch(URL)
        with patch.dict(os.environ, {'SPOTIFY_METADATA_MODE': 'disabled'}):
            self.assert_error('disabled')
        self.assertIsNone(self.client._token)
        self.client.fetch(URL)
        self.assertEqual(self.post.call_count, 2)

    def test_configuration_errors_without_network(self):
        for variable, value, code in (
            ('SPOTIFY_METADATA_MODE', 'oembed', 'invalid_configuration'),
            ('SPOTIFY_CLIENT_ID', '', 'missing_credentials'),
            ('SPOTIFY_CLIENT_SECRET', '', 'missing_credentials'),
            ('SPOTIFY_MARKET', '', 'invalid_configuration'),
            ('SPOTIFY_MARKET', 'France', 'invalid_configuration'),
            ('SPOTIFY_CLIENT_ID', 'user:password', 'invalid_configuration'),
            ('SPOTIFY_CLIENT_SECRET', 'bad\r\nsecret', 'invalid_configuration'),
        ):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: value}):
                self.assert_error(code)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_invalid_sources_without_network(self):
        for value in ('https://evil.test', 'https://youtu.be/dQw4w9WgXcQ',
                      replace(normalize_source_url(URL), source_id='wrong')):
            with self.subTest(value=value), self.assertRaises(MetadataURLValidationError):
                self.client.fetch(value)
        self.post.assert_not_called()
        self.get.assert_not_called()

    def test_token_reused_until_early_expiry(self):
        self.client.fetch(URL)
        self.clock.return_value = 3669
        self.client.fetch(URL)
        self.post.assert_called_once()
        self.clock.return_value = 3670
        self.client.fetch(URL)
        self.assertEqual(self.post.call_count, 2)

    def test_credential_rotation_invalidates_cache(self):
        self.client.fetch(URL)
        with patch.dict(os.environ, {'SPOTIFY_CLIENT_SECRET': 'rotated-fake-secret'}):
            self.client.fetch(URL)
        self.assertEqual(self.post.call_count, 2)

    def test_401_renews_exactly_once(self):
        denied = response({}, 401)
        self.get.side_effect = [denied, self.track_response]
        self.assertEqual(self.client.fetch(URL).title, 'Titre')
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.get.call_count, 2)
        denied.close.assert_called_once()

    def test_repeated_401_stops_and_clears_token(self):
        denied = response({}, 401)
        self.get.return_value = denied
        self.assert_error('access_denied')
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(self.post.call_count, 2)
        self.assertIsNone(self.client._token)
        self.assertEqual(denied.close.call_count, 2)

    def test_missing_fields_are_optional(self):
        self.track_response.json.return_value = {'id': TRACK}
        metadata = self.client.fetch(URL)
        for field in ('title', 'artist', 'album', 'duration_seconds', 'artwork_url',
                      'publication_date', 'publication_date_precision'):
            self.assertIsNone(getattr(metadata, field))

    def test_partial_artists_and_artwork(self):
        self.track_response.json.return_value = {'id': TRACK, 'artists': [{}, {'name': None}, {'name': ' A '}],
            'album': {'images': [{}, {'url': None}, {'url': 'https://i.scdn.co/fake'}]}}
        result = self.client.fetch(URL)
        self.assertEqual(result.artist, 'A')
        self.assertEqual(result.artwork_url, 'https://i.scdn.co/fake')

    def test_release_date_precision(self):
        for value, precision in (('2020', 'year'), ('2020-02', 'month'), ('2020-02-29', 'day'), ('2020', None)):
            self.track_response.json.return_value['album'].update(release_date=value, release_date_precision=precision)
            result = self.client.fetch(URL)
            self.assertEqual((result.publication_date, result.publication_date_precision), (value, precision))

    def test_zero_duration(self):
        self.track_response.json.return_value['duration_ms'] = 0
        self.assertEqual(self.client.fetch(URL).duration_seconds, 0)

    def test_null_optional_fields_and_unlabelled_bad_date(self):
        self.track_response.json.return_value = {'id': TRACK, 'artists': None, 'album': None, 'duration_ms': None}
        self.assertIsNone(self.client.fetch(URL).artist)
        self.track_response.json.return_value['album'] = {'release_date': 'not-a-date'}
        self.assert_error('invalid_response')
        self.track_response.json.return_value['album'] = {'release_date': '2020-02-30'}
        self.assert_error('invalid_response')

    def test_malformed_fields(self):
        for field, value in (('name', []), ('artists', {}), ('artists', [None]),
                             ('artists', [{'name': 123}]), ('album', []),
                             ('duration_ms', True), ('duration_ms', -1), ('duration_ms', '100'),
                             ('duration_ms', float('inf')), ('type', 'episode')):
            with self.subTest(field=field, value=value):
                self.track_response.json.return_value = {**copy.deepcopy(PAYLOAD), field: value}
                self.assert_error('invalid_response')

    def test_malformed_album_fields(self):
        for field, value in (('name', []), ('images', {}), ('images', [None]),
                             ('images', [{'url': 'javascript:bad'}]),
                             ('release_date', '2020-02-30'), ('release_date_precision', 'second')):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(PAYLOAD)
                payload['album'][field] = value
                self.track_response.json.return_value = payload
                self.assert_error('invalid_response')

    def test_wrong_identity_and_relinking(self):
        self.track_response.json.return_value = {'id': 'another'}
        self.assert_error('invalid_response')
        self.track_response.json.return_value['linked_from'] = {'id': TRACK}
        self.assert_error('source_relinked')
        self.assertEqual(self.get.call_count, 2)  # One per explicit call, no substitute lookup.

    def test_malformed_track_envelopes(self):
        for payload in (None, [], {}, {'error': 'unexpected'}):
            self.track_response.json.return_value = payload
            self.assert_error('invalid_response')
        self.track_response.json.side_effect = ValueError(SECRET)
        self.assert_error('invalid_response')

    def test_invalid_tokens_not_cached(self):
        for token in ({}, {'access_token': ''}, {'access_token': 'bad\r\nheader'},
                      {'token_type': 'Basic'}, {'expires_in': 0}, {'expires_in': True},
                      {'expires_in': '3600'}, {'expires_in': 999999}):
            with self.subTest(token=token):
                self.token_response.json.return_value = {
                    'access_token': TOKEN, 'token_type': 'Bearer', 'expires_in': 3600, **token}
                if token == {}:
                    self.token_response.json.return_value = {}
                self.assert_error('invalid_response')
                self.assertIsNone(self.client._token)
        self.get.assert_not_called()

    def test_token_expires_during_request(self):
        self.clock.side_effect = [100, 4000]
        self.assert_error('token_expired', True)
        self.get.assert_not_called()

    def test_token_invalid_json(self):
        self.token_response.json.side_effect = ValueError(SECRET)
        self.assert_error('invalid_response')
        self.token_response.close.assert_called_once()
        self.get.assert_not_called()

    def test_http_errors_on_both_endpoints(self):
        for token_endpoint in (True, False):
            for status, code, retryable in ((400, 'access_denied' if token_endpoint else 'http_error', False),
                    (401, 'access_denied', False), (403, 'access_denied', False),
                    (404, 'http_error' if token_endpoint else 'not_found', False),
                    (429, 'rate_limited', True), (500, 'service_unavailable', True),
                    (302, 'http_error', False)):
                with self.subTest(token_endpoint=token_endpoint, status=status):
                    self.client = SpotifyMetadataClient()
                    self.post.reset_mock()
                    self.get.reset_mock()
                    self.token_response.status_code = status if token_endpoint else 200
                    self.track_response.status_code = 200 if token_endpoint else status
                    self.assert_error(code, retryable)
                    self.assertEqual(self.post.call_count, 2 if not token_endpoint and status == 401 else 1)
                    self.assertEqual(self.get.call_count, 0 if token_endpoint else (2 if status == 401 else 1))

    def test_quota_and_retry_after(self):
        for token_endpoint in (True, False):
            self.client = SpotifyMetadataClient()
            failed = response({'error': {'reason': 'QUOTA_EXCEEDED', 'message': SECRET}}, 429, {'Retry-After': '60'})
            if token_endpoint:
                self.post.return_value = failed
            else:
                self.post.return_value = self.token_response
                self.get.return_value = failed
            error = self.assert_error('quota_exceeded', True)
            self.assertEqual(error.retry_after_seconds, 60)
            failed.close.assert_called_once()

    def test_malformed_quota_response_and_delay(self):
        self.get.return_value = response({'error': []}, 429, {'Retry-After': 'invalid'})
        error = self.assert_error('rate_limited', True)
        self.assertIsNone(error.retry_after_seconds)

    def test_non_json_service_error(self):
        self.track_response.status_code = 503
        self.track_response.json.side_effect = ValueError(SECRET)
        self.assert_error('service_unavailable', True)

    def test_non_json_quota_response(self):
        self.track_response.status_code = 429
        self.track_response.json.side_effect = ValueError(SECRET)
        self.assert_error('rate_limited', True)
        self.get.assert_called_once()

    def test_network_errors_on_both_endpoints(self):
        for token_endpoint in (True, False):
            for failure, code in ((requests.Timeout, 'timeout'), (requests.ConnectionError, 'network_error')):
                with self.subTest(token_endpoint=token_endpoint, failure=failure):
                    self.client = SpotifyMetadataClient()
                    self.post.side_effect = failure(SECRET) if token_endpoint else None
                    self.get.side_effect = None if token_endpoint else failure(TOKEN)
                    error = self.assert_error(code, True)
                    self.assertTrue(error.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
