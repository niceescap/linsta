from dataclasses import FrozenInstanceError, asdict
import unittest
from unittest.mock import patch

from metadata import MetadataURLValidationError, TrackMetadata, normalize_source_url


VIDEO = 'dQw4w9WgXcQ'
TRACK = '4uLU6hMCjMI75M1A2tKUQC'


class MetadataURLTests(unittest.TestCase):
    def assert_invalid(self, url, code):
        with self.assertRaises(MetadataURLValidationError) as error:
            normalize_source_url(url)
        self.assertEqual(error.exception.code, code)

    def test_youtube_variants(self):
        urls = [
            f'https://www.youtube.com/watch?v={VIDEO}',
            f'http://youtube.com/watch?v={VIDEO}&t=42&list=playlist',
            f'https://m.youtube.com/watch?feature=share&v={VIDEO}',
            f'https://music.youtube.com/watch?v={VIDEO}',
            f'https://youtu.be/{VIDEO}?si=tracking#t=30',
            f'https://www.youtu.be/{VIDEO}/',
            f'HTTPS://WWW.YOUTUBE.COM/watch?v={VIDEO}',
            f' https://youtu.be/{VIDEO} ',
        ]
        urls += [f'https://www.youtube.com/{path}/{VIDEO}/?t=1'
                 for path in ('shorts', 'live', 'embed', 'v')]
        urls += [f'https://{host}/embed/{VIDEO}' for host in ('youtube-nocookie.com', 'www.youtube-nocookie.com')]
        for url in urls:
            with self.subTest(url=url):
                source = normalize_source_url(url)
                self.assertEqual(source.platform, 'youtube')
                self.assertEqual(source.source_id, VIDEO)
                self.assertEqual(source.source_url, url.strip())
                self.assertEqual(source.canonical_url, f'https://www.youtube.com/watch?v={VIDEO}')

    def test_spotify_variants(self):
        for prefix in ('', 'embed/', 'intl-fr/', 'intl-en/embed/'):
            for suffix in ('', '/', '?si=tracking#fragment'):
                url = f'https://open.spotify.com/{prefix}track/{TRACK}{suffix}'
                with self.subTest(url=url):
                    source = normalize_source_url(url)
                    self.assertEqual((source.platform, source.source_id), ('spotify', TRACK))
                    self.assertEqual(source.canonical_url, f'https://open.spotify.com/track/{TRACK}')

    def test_malformed_urls(self):
        for url in (None, 42, '', ' ', 'https://', 'https://[bad',
                    f'https://user@youtube.com/watch?v={VIDEO}',
                    f'https://youtube.com:443/watch?v={VIDEO}',
                    f'https://youtube.com\\@evil.test/watch?v={VIDEO}',
                    f'https://youtube.com/watch?v={VIDEO}\n',
                    f'\thttps://youtu.be/{VIDEO}',
                    f'https://youtu.be/{VIDEO}?x=%zz',
                    f'https://youtu.be/{VIDEO}?x=space here',
                    f'https://youtu.be/{VIDEO}?x=\x7f',
                    'https://youtu.be/' + 'a' * 4096):
            with self.subTest(url=url):
                self.assert_invalid(url, 'invalid_url')

    def test_dangerous_or_missing_schemes(self):
        for url in ('javascript:alert(1)', 'data:text/plain,test', 'file:///etc/passwd',
                    f'ftp://youtube.com/watch?v={VIDEO}', f'//youtu.be/{VIDEO}',
                    f'youtu.be/{VIDEO}', f'spotify:track:{TRACK}'):
            with self.subTest(url=url):
                self.assert_invalid(url, 'unsupported_scheme')

    def test_deceptive_and_unsupported_hosts(self):
        for host in ('youtube.com.evil.test', 'notyoutube.com', 'evilyoutu.be',
                     'open.spotify.com.evil.test', 'evilspotify.com', 'spotify.com',
                     'www.open.spotify.com', 'youtube.com.', 'youtube%2ecom',
                     'yоutube.com', 'localhost', '127.0.0.1', 'example.test',
                     'spotify.link', 'www.youtube.com.evil.test'):
            with self.subTest(host=host):
                self.assert_invalid(f'https://{host}/watch?v={VIDEO}', 'unsupported_host')

    def test_unsupported_resources(self):
        for url in (
            'https://youtube.com/playlist?list=123', 'https://youtube.com/@artist',
            'https://youtube.com/channel/channel', 'https://youtube.com/results?search_query=music',
            'https://youtube.com/redirect?q=https://example.test',
            f'https://youtube.com/embed/{VIDEO}/extra',
            f'https://youtube.com/%77atch?v={VIDEO}',
            f'https://youtube-nocookie.com/watch?v={VIDEO}', 'https://youtu.be/',
            f'https://open.spotify.com/album/{TRACK}', f'https://open.spotify.com/playlist/{TRACK}',
            f'https://open.spotify.com/artist/{TRACK}', f'https://open.spotify.com/episode/{TRACK}',
            f'https://open.spotify.com/track/{TRACK}/extra',
            f'https://open.spotify.com/track%2f{TRACK}',
        ):
            with self.subTest(url=url):
                self.assert_invalid(url, 'unsupported_resource')

    def test_bad_video_ids(self):
        for source_id in ('', 'short', VIDEO + 'x', 'abc!efghijk', 'é' * 11, VIDEO + '%2fextra'):
            with self.subTest(source_id=source_id):
                self.assert_invalid(f'https://youtube.com/watch?v={source_id}', 'invalid_source_id')
        self.assert_invalid('https://youtube.com/watch', 'invalid_source_id')

    def test_bad_spotify_ids(self):
        for source_id in ('short', TRACK + 'x', '_' * 22, 'é' * 22):
            with self.subTest(source_id=source_id):
                self.assert_invalid(f'https://open.spotify.com/track/{source_id}', 'invalid_source_id')

    def test_duplicate_video_id_parameters(self):
        for query in (f'v={VIDEO}&v={VIDEO}', f'v=&v={VIDEO}', f'v={VIDEO}&%76={VIDEO}'):
            self.assert_invalid(f'https://youtube.com/watch?{query}', 'ambiguous_source')

    def test_query_parameter_limit(self):
        self.assert_invalid(f'https://youtube.com/watch?v={VIDEO}&' + '&'.join(['x=1'] * 100), 'invalid_url')

    def test_identity_case_and_canonical_idempotence(self):
        for url in (f'https://youtu.be/{VIDEO}', f'https://open.spotify.com/track/{TRACK}'):
            source = normalize_source_url(url)
            canonical = normalize_source_url(source.canonical_url)
            self.assertEqual(source.source_id, canonical.source_id)
            self.assertEqual(source.canonical_url, canonical.canonical_url)

    def test_optional_metadata_contract_and_immutability(self):
        source = normalize_source_url(f'https://youtu.be/{VIDEO}')
        metadata = TrackMetadata(source)
        self.assertTrue(all(value is None for key, value in asdict(metadata).items() if key != 'source'))
        complete = TrackMetadata(source, title='Titre', artist='Artiste', channel='Chaîne',
                                 duration_seconds=123.5, artwork_url='https://example.test/cover.jpg',
                                 publication_date='2020', publication_date_precision='year',
                                 youtube_view_count=0)
        self.assertEqual(complete.duration_seconds, 123.5)
        self.assertEqual(complete.youtube_view_count, 0)
        with self.assertRaises(FrozenInstanceError):
            source.source_id = 'changed'
        with self.assertRaises(FrozenInstanceError):
            complete.title = 'changed'

    def test_no_network_access(self):
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            normalize_source_url(f'https://youtu.be/{VIDEO}')
            normalize_source_url(f'https://open.spotify.com/track/{TRACK}')


if __name__ == '__main__':
    unittest.main()
