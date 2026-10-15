import copy
import os
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import requests

from metadata import (MetadataRetrievalError, MetadataURLValidationError,
                      fetch_youtube_metadata, normalize_source_url)
from metadata.youtube import ENDPOINT, TIMEOUT, parse_youtube_duration


VIDEO = 'dQw4w9WgXcQ'
URL = f'https://youtu.be/{VIDEO}'
# Explicitly fake, never a provider credential.
FAKE_KEY = 'unit-test-placeholder'
PAYLOAD = {'items': [{'id': VIDEO, 'snippet': {
    'title': 'Titre', 'channelTitle': 'Chaîne', 'description': 'Description',
    'publishedAt': '2020-02-29T12:34:56Z',
    'thumbnails': {'high': {'url': 'https://i.ytimg.com/vi/test/hqdefault.jpg'},
                   'default': {'url': 'https://i.ytimg.com/vi/test/default.jpg'}}},
    'contentDetails': {'duration': 'PT3M12S'}, 'statistics': {'viewCount': '12345'}}]}


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        # No test may open a real socket, even if HTTP mocking is accidentally lost.
        self.socket = patch('socket.socket', side_effect=AssertionError('Network forbidden'))
        self.socket.start()
        self.addCleanup(self.socket.stop)
        environment = patch.dict(os.environ, {'YOUTUBE_API_KEY': FAKE_KEY})
        environment.start()
        self.addCleanup(environment.stop)
        self.response = Mock(status_code=200)
        self.response.json.return_value = copy.deepcopy(PAYLOAD)
        http = patch('metadata.youtube.requests.get', return_value=self.response)
        self.get = http.start()
        self.addCleanup(http.stop)

    def assert_error(self, code, *, retryable=False):
        with self.assertRaises(MetadataRetrievalError) as caught:
            fetch_youtube_metadata(URL)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.retryable, retryable)
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        return caught.exception

    def test_complete_response_and_fixed_request(self):
        result = fetch_youtube_metadata(normalize_source_url(URL))
        self.assertEqual(result.title, 'Titre')
        self.assertEqual(result.channel, 'Chaîne')
        self.assertIsNone(result.artist)
        self.assertEqual(result.description, 'Description')
        self.assertEqual(result.duration_seconds, 192)
        self.assertEqual(result.publication_date, '2020-02-29T12:34:56Z')
        self.assertEqual(result.publication_date_precision, 'second')
        self.assertEqual(result.youtube_view_count, 12345)
        self.assertEqual(result.artwork_url, PAYLOAD['items'][0]['snippet']['thumbnails']['high']['url'])
        self.get.assert_called_once_with(ENDPOINT, params={
            'part': 'snippet,contentDetails,statistics', 'id': VIDEO},
            headers={'X-Goog-Api-Key': FAKE_KEY, 'Accept': 'application/json'},
            timeout=TIMEOUT, allow_redirects=False)
        self.response.close.assert_called_once()

    def test_missing_optional_sections(self):
        self.response.json.return_value = {'items': [{'id': VIDEO}]}
        result = fetch_youtube_metadata(URL)
        for field in ('title', 'artist', 'channel', 'description', 'duration_seconds',
                      'publication_date', 'publication_date_precision', 'artwork_url', 'youtube_view_count'):
            self.assertIsNone(getattr(result, field))

    def test_missing_optional_fields_and_nulls(self):
        self.response.json.return_value = {'items': [{'id': VIDEO, 'snippet': {
            'title': '  ', 'channelTitle': None, 'thumbnails': {}},
            'contentDetails': None, 'statistics': {'viewCount': None}}]}
        result = fetch_youtube_metadata(URL)
        self.assertIsNone(result.title)
        self.assertIsNone(result.duration_seconds)
        self.assertIsNone(result.youtube_view_count)

    def test_duration_conversion(self):
        for duration, seconds in {'PT0S': 0, 'PT42S': 42, 'PT2M': 120, 'PT1H2M3S': 3723,
                                  'P2DT3H': 183600, 'P1D': 86400, 'PT0.25S': .25,
                                  'PT100H': 360000}.items():
            with self.subTest(duration=duration):
                self.assertEqual(parse_youtube_duration(duration), seconds)

    def test_invalid_durations(self):
        for value in (None, 42, '', 'P', 'PT', 'P1DT', 'PT-2S', 'P1M', 'P1Y', 'P1W',
                      'PT1S2M', 'PTNaNS', 'PT1.2.3S', ' PT2S', 'PT' + '9' * 101 + 'S'):
            with self.subTest(value=value), self.assertRaises(MetadataRetrievalError) as caught:
                parse_youtube_duration(value)
            self.assertEqual(caught.exception.code, 'invalid_response')

    def test_zero_views_and_duration(self):
        self.response.json.return_value['items'][0]['statistics']['viewCount'] = '0'
        self.response.json.return_value['items'][0]['contentDetails']['duration'] = 'PT0S'
        result = fetch_youtube_metadata(URL)
        self.assertEqual(result.youtube_view_count, 0)
        self.assertEqual(result.duration_seconds, 0)

    def test_thumbnail_priority_and_fallback(self):
        item = self.response.json.return_value['items'][0]
        item['snippet']['thumbnails'] = {}
        for size in ('default', 'medium', 'high', 'standard', 'maxres'):
            item['snippet']['thumbnails'][size] = {'url': f'https://i.ytimg.com/{size}.jpg'}
            self.assertEqual(fetch_youtube_metadata(URL).artwork_url, f'https://i.ytimg.com/{size}.jpg')

    def test_publication_offsets_and_fractional_seconds(self):
        for publication in ('2020-02-29T12:34:56.123Z', '2020-02-29T12:34:56+02:00'):
            self.response.json.return_value['items'][0]['snippet']['publishedAt'] = publication
            self.assertEqual(fetch_youtube_metadata(URL).publication_date, publication)

    def test_malformed_error_envelopes(self):
        self.response.status_code = 403
        for payload in (None, [], {'error': None}, {'error': {'errors': None}},
                        {'error': {'errors': [None, {'reason': []}]}}):
            self.response.json.return_value = payload
            self.assert_error('access_denied')

    def test_not_found(self):
        self.response.json.return_value = {'items': []}
        self.assert_error('not_found')
        self.response.close.assert_called_once()

    def test_missing_key_no_request(self):
        for key in ('', '   '):
            with patch.dict(os.environ, {'YOUTUBE_API_KEY': key}):
                self.assert_error('missing_api_key')
        self.get.assert_not_called()

    def test_key_read_at_call_time(self):
        with patch.dict(os.environ, {'YOUTUBE_API_KEY': 'second-fake-placeholder'}):
            fetch_youtube_metadata(URL)
        self.assertEqual(self.get.call_args.kwargs['headers']['X-Goog-Api-Key'], 'second-fake-placeholder')

    def test_source_validation_before_request(self):
        for source in ('https://evil.test/watch?v=' + VIDEO,
                       'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC',
                       replace(normalize_source_url(URL), source_id='different'),
                       replace(normalize_source_url(URL), canonical_url='https://evil.test')):
            with self.subTest(source=source), self.assertRaises(MetadataURLValidationError):
                fetch_youtube_metadata(source)
        self.get.assert_not_called()

    def test_network_failures_sanitized_and_not_retried(self):
        for failure, code in ((requests.Timeout, 'timeout'), (requests.ConnectionError, 'network_error'),
                              (requests.RequestException, 'network_error')):
            self.get.reset_mock()
            self.get.side_effect = failure('sensitive ' + FAKE_KEY)
            error = self.assert_error(code, retryable=True)
            self.assertTrue(error.__suppress_context__)
            self.get.assert_called_once()

    def test_http_failure_classification(self):
        for status, reason, code, retryable in (
            (403, 'quotaExceeded', 'quota_exceeded', True),
            (403, 'dailyLimitExceeded', 'quota_exceeded', True),
            (403, 'userRateLimitExceeded', 'rate_limited', True),
            (429, '', 'rate_limited', True), (500, '', 'service_unavailable', True),
            (503, '', 'service_unavailable', True), (404, 'videoNotFound', 'not_found', False),
            (401, '', 'access_denied', False), (403, 'forbidden', 'access_denied', False),
            (400, 'keyInvalid', 'access_denied', False), (400, '', 'http_error', False),
            (302, '', 'http_error', False),
        ):
            with self.subTest(status=status, reason=reason):
                self.response.status_code = status
                self.response.json.return_value = {'error': {'message': FAKE_KEY, 'errors': [{'reason': reason}]}}
                self.assert_error(code, retryable=retryable)

    def test_non_json_http_error(self):
        self.response.status_code = 503
        self.response.json.side_effect = ValueError(FAKE_KEY)
        self.assert_error('service_unavailable', retryable=True)
        self.response.close.assert_called_once()

    def test_invalid_json_success(self):
        self.response.json.side_effect = ValueError(FAKE_KEY)
        self.assert_error('invalid_response')
        self.response.close.assert_called_once()

    def test_malformed_payloads(self):
        for payload in (None, [], {}, {'items': None}, {'items': {}}, {'items': [None]},
                        {'items': [{'id': 'different'}]}, {'items': [{'id': VIDEO}, {'id': VIDEO}]}):
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                self.assert_error('invalid_response')

    def test_malformed_optional_fields(self):
        for section, field, value in (
            ('snippet', 'title', 12), ('snippet', 'channelTitle', []),
            ('snippet', 'publishedAt', '2020-02-30T00:00:00Z'),
            ('snippet', 'publishedAt', '2020'), ('snippet', 'thumbnails', []),
            ('snippet', 'thumbnails', {'high': {'url': 'javascript:bad'}}),
            ('statistics', 'viewCount', -1), ('statistics', 'viewCount', True),
            ('statistics', 'viewCount', '-1'), ('statistics', 'viewCount', 'NaN'),
            ('contentDetails', 'duration', 'invalid'),
        ):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(PAYLOAD)
                payload['items'][0][section][field] = value
                self.response.json.return_value = payload
                self.assert_error('invalid_response')

    def test_malformed_sections(self):
        for section in ('snippet', 'statistics', 'contentDetails'):
            with self.subTest(section=section):
                self.response.json.return_value = {'items': [{'id': VIDEO, section: []}]}
                self.assert_error('invalid_response')


if __name__ == '__main__':
    unittest.main()
