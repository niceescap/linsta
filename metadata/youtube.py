"""YouTube Data API v3 adapter: metadata only, no scraping or media download.

Reads YOUTUBE_API_KEY at call time. No dotenv/config imports or import-time I/O.
Requests go only to the official fixed endpoint, with redirects disabled.
Missing optional fields remain None; malformed supplied fields raise a sanitized
error. No automatic retries (in particular, no retry loop on quota exhaustion).
"""
import math
import os
import re
from datetime import datetime
from urllib.parse import urlsplit

import requests

from .errors import MetadataRetrievalError, MetadataURLValidationError
from .models import NormalizedSource, TrackMetadata
from .urls import normalize_source_url


ENDPOINT = 'https://www.googleapis.com/youtube/v3/videos'
TIMEOUT = (3.05, 10)  # connect and read timeout in seconds
_DURATION = re.compile(
    r'P(?:(?P<days>[0-9]+)D)?'
    r'(?:T(?:(?P<hours>[0-9]+)H)?(?:(?P<minutes>[0-9]+)M)?'
    r'(?:(?P<seconds>[0-9]+(?:\.[0-9]+)?)S)?)?', re.ASCII
)


def _invalid():
    raise MetadataRetrievalError('invalid_response', 'Réponse YouTube incorrecte.')


def parse_youtube_duration(value: str) -> float:
    """Convert fixed ISO 8601 day/hour/minute/second durations to seconds.

    Calendar years/months and weeks are not YouTube video duration formats.
    """
    if not isinstance(value, str) or len(value) > 100:
        _invalid()
    match = _DURATION.fullmatch(value)
    if not match or not any(match.groupdict().values()) or value.endswith('T'):
        _invalid()
    total = sum(float(match[name] or 0) * multiplier for name, multiplier in
                (('days', 86400), ('hours', 3600), ('minutes', 60), ('seconds', 1)))
    if not math.isfinite(total):
        _invalid()
    return total


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid()
    return value.strip() or None


def _section(item, name):
    value = item.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        _invalid()
    return value


def _publication(value):
    value = _text(value)
    if value is None:
        return None
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                        r'(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})', value):
        _invalid()
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        _invalid()
    return value


def _thumbnail(snippet):
    thumbnails = _section(snippet, 'thumbnails')
    for size in ('maxres', 'standard', 'high', 'medium', 'default'):
        thumbnail = _section(thumbnails, size)
        url = _text(thumbnail.get('url'))
        if url is not None:
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme == 'https' and parsed.hostname
                         and parsed.username is None and parsed.password is None
                         and parsed.port is None and not any(c.isspace() or ord(c) < 32 for c in url))
            except ValueError:
                valid = False
            if not valid:
                _invalid()
            return url
    return None


def _http_error(response):
    status = response.status_code
    # Read only documented reason codes, never forward provider messages.
    reasons = set()
    try:
        payload = response.json()
        error = payload.get('error', {}) if isinstance(payload, dict) else {}
        errors = error.get('errors', []) if isinstance(error, dict) else []
        if isinstance(errors, list):
            reasons = {e['reason'] for e in errors if isinstance(e, dict)
                       and isinstance(e.get('reason'), str)}
    except ValueError:
        pass
    if status == 429 or reasons & {'rateLimitExceeded', 'userRateLimitExceeded'}:
        raise MetadataRetrievalError('rate_limited', 'Limite de requêtes YouTube atteinte.', retryable=True)
    if reasons & {'quotaExceeded', 'dailyLimitExceeded', 'dailyLimitExceededUnreg'}:
        raise MetadataRetrievalError('quota_exceeded', 'Quota YouTube épuisé.', retryable=True)
    if status == 404:
        raise MetadataRetrievalError('not_found', 'Vidéo YouTube introuvable ou inaccessible.')
    if status in (401, 403) or reasons & {'keyInvalid', 'accessNotConfigured', 'ipRefererBlocked'}:
        raise MetadataRetrievalError('access_denied', 'Accès à YouTube refusé ; vérifier la configuration.')
    if 500 <= status <= 599:
        raise MetadataRetrievalError('service_unavailable', 'Service YouTube indisponible.', retryable=True)
    raise MetadataRetrievalError('http_error', 'Échec de la requête YouTube.')


def fetch_youtube_metadata(source) -> TrackMetadata:
    """Fetch a URL or NormalizedSource, using the current YOUTUBE_API_KEY.

    Existence/visibility failures cannot distinguish deleted and private videos.
    Caller must not log raw HTTP exceptions or request objects containing keys.
    """
    if isinstance(source, NormalizedSource):
        checked = normalize_source_url(source.source_url)
        if checked != source:
            raise MetadataURLValidationError('invalid_source', 'Identité source incohérente.')
    else:
        checked = normalize_source_url(source)
    if checked.platform != 'youtube':
        raise MetadataURLValidationError('unsupported_platform', 'Une vidéo YouTube est requise.')
    key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    if not key:
        raise MetadataRetrievalError('missing_api_key', 'Variable YOUTUBE_API_KEY manquante.')
    try:
        response = requests.get(ENDPOINT, params={
            'part': 'snippet,contentDetails,statistics', 'id': checked.source_id,
        }, headers={'X-Goog-Api-Key': key, 'Accept': 'application/json'},
            timeout=TIMEOUT, allow_redirects=False)
    except requests.Timeout:
        raise MetadataRetrievalError('timeout', 'Délai de réponse YouTube dépassé.', retryable=True) from None
    except requests.RequestException:
        raise MetadataRetrievalError('network_error', 'Erreur réseau lors de l’accès à YouTube.', retryable=True) from None
    try:
        if response.status_code != 200:
            _http_error(response)
        try:
            payload = response.json()
        except ValueError:
            raise MetadataRetrievalError('invalid_response', 'Réponse JSON YouTube incorrecte.') from None
        if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
            _invalid()
        items = payload['items']
        if not items:
            raise MetadataRetrievalError('not_found', 'Vidéo YouTube introuvable ou inaccessible.')
        if len(items) != 1 or not isinstance(items[0], dict) or items[0].get('id') != checked.source_id:
            _invalid()
        item = items[0]
        snippet = _section(item, 'snippet')
        details = _section(item, 'contentDetails')
        statistics = _section(item, 'statistics')
        duration = details.get('duration')
        views = statistics.get('viewCount')
        if views is not None:
            if not isinstance(views, str) or not re.fullmatch('[0-9]+', views, flags=re.ASCII) or len(views) > 30:
                _invalid()
            views = int(views)
        publication = _publication(snippet.get('publishedAt'))
        return TrackMetadata(
            source=checked, title=_text(snippet.get('title')),
            artist=None, channel=_text(snippet.get('channelTitle')),
            description=_text(snippet.get('description')),
            duration_seconds=parse_youtube_duration(duration) if duration is not None else None,
            publication_date=publication, publication_date_precision='second' if publication else None,
            artwork_url=_thumbnail(snippet), youtube_view_count=views,
        )
    finally:
        response.close()
