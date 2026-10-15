"""Pure URL normalization for individual videos/tracks, without URL resolution.

Only HTTP(S), exact allowlisted hosts and known resource paths are accepted.
Explicit ports, credentials, encoded paths and redirect/share wrappers are not
supported. Query tracking, fragments and playlist context on a video URL are
discarded; playlist-only URLs are rejected. IDs remain case sensitive.
Validation proves syntax/resource type, not provider existence or ownership.
"""
import re
from urllib.parse import parse_qs, urlsplit

from .errors import MetadataURLValidationError
from .models import NormalizedSource


YOUTUBE_HOSTS = frozenset(('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'))
SHORT_HOSTS = frozenset(('youtu.be', 'www.youtu.be'))
EMBED_HOSTS = frozenset(('youtube-nocookie.com', 'www.youtube-nocookie.com'))
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}', re.ASCII)
TRACK_ID = re.compile(r'[A-Za-z0-9]{22}', re.ASCII)


def _fail(code, message):
    raise MetadataURLValidationError(code, message)


def normalize_source_url(value: str) -> NormalizedSource:
    """Return canonical source identity or raise MetadataURLValidationError.

    Supported YouTube paths: watch?v=, shorts/, live/, embed/, v/, youtu.be/.
    Spotify supports /track/, /embed/track/ and optional /intl-xx/ prefixes.
    Spotify short links require redirects and are deliberately unsupported.
    """
    if not isinstance(value, str) or not value or len(value) > 4096:
        _fail('invalid_url', 'Une URL absolue est requise (4096 caractères maximum).')
    # urlsplit silently strips some controls: reject them before parsing.
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail('invalid_url', 'Les caractères de contrôle sont interdits.')
    value = value.strip(' ')
    if not value or any(char.isspace() for char in value) or '\\' in value:
        _fail('invalid_url', 'URL mal formée : espaces ou antislashs interdits.')
    if re.search(r'%(?![0-9A-Fa-f]{2})', value):
        _fail('invalid_url', 'Encodage URL invalide.')
    try:
        parsed = urlsplit(value)
    except ValueError:
        _fail('invalid_url', 'URL mal formée.')
    if parsed.scheme.lower() not in ('http', 'https'):
        _fail('unsupported_scheme', 'Seules les URL HTTP et HTTPS sont acceptées.')
    if not parsed.netloc or '@' in parsed.netloc or ':' in parsed.netloc:
        _fail('invalid_url', 'Hôte requis ; identifiants et ports explicites interdits.')
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS | SHORT_HOSTS | EMBED_HOSTS | {'open.spotify.com'}:
        _fail('unsupported_host', 'Domaine non pris en charge.')
    if '%' in parsed.path:
        _fail('unsupported_resource', 'Les chemins encodés ne sont pas pris en charge.')
    if host == 'open.spotify.com':
        match = re.fullmatch(r'/(?:intl-[a-zA-Z]{2}/)?(?:embed/)?track/([^/]+)/?', parsed.path)
        if not match:
            _fail('unsupported_resource', 'Seuls les morceaux Spotify sont acceptés.')
        source_id = match[1]
        if not TRACK_ID.fullmatch(source_id):
            _fail('invalid_source_id', 'Identifiant Spotify invalide.')
        return NormalizedSource('spotify', source_id, value, f'https://open.spotify.com/track/{source_id}')
    if host in SHORT_HOSTS:
        match = re.fullmatch(r'/([^/]+)/?', parsed.path)
        source_id = match[1] if match else None
    elif parsed.path == '/watch' and host in YOUTUBE_HOSTS:
        try:
            ids = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=100).get('v', [])
        except ValueError:
            _fail('invalid_url', 'Trop de paramètres dans cette URL.')
        if len(ids) > 1:
            _fail('ambiguous_source', 'Plusieurs identifiants vidéo sont interdits.')
        source_id = ids[0] if ids else ''
    else:
        paths = 'embed' if host in EMBED_HOSTS else 'embed|shorts|live|v'
        match = re.fullmatch(rf'/(?:{paths})/([^/]+)/?', parsed.path)
        source_id = match[1] if match else None
    if source_id is None:
        _fail('unsupported_resource', 'Seules les vidéos YouTube individuelles sont acceptées.')
    if not VIDEO_ID.fullmatch(source_id):
        _fail('invalid_source_id', 'Identifiant vidéo YouTube invalide.')
    return NormalizedSource('youtube', source_id, value, f'https://www.youtube.com/watch?v={source_id}')
