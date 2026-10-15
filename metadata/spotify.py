"""Opt-in Spotify catalog adapter. No audio, scraping, oEmbed or persistence.

Disabled unless SPOTIFY_METADATA_MODE=web_api. Required environment variables:
SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_MARKET (two-letter market).
No application config/.env imports and no network activity at import time.
Use one SpotifyMetadataClient instance to reuse its in-memory token. Its lock
serializes calls; this small adapter is not intended as a high-throughput service.
Activation here is a technical switch, not commercial permission from Spotify.
Artwork is returned only as data, never downloaded or added to the radio catalog.
"""
import hashlib
import os
import re
import threading
import time
from datetime import date
from urllib.parse import urlsplit

import requests

from .errors import MetadataRetrievalError, MetadataURLValidationError
from .models import NormalizedSource, TrackMetadata
from .urls import normalize_source_url


TOKEN_URL = 'https://accounts.spotify.com/api/token'
TRACK_URL = 'https://api.spotify.com/v1/tracks/'
TIMEOUT = (3.05, 10)


def _invalid():
    raise MetadataRetrievalError('invalid_response', 'Réponse Spotify incorrecte.')


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid()
    return value.strip() or None


def _object(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        _invalid()
    return value


def _json(response):
    try:
        payload = response.json()
    except ValueError:
        raise MetadataRetrievalError('invalid_response', 'Réponse JSON Spotify incorrecte.') from None
    if not isinstance(payload, dict):
        _invalid()
    return payload


def _request(method, url, **kwargs):
    try:
        return method(url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
    except requests.Timeout:
        raise MetadataRetrievalError('timeout', 'Délai Spotify dépassé.', retryable=True) from None
    except requests.RequestException:
        raise MetadataRetrievalError('network_error', 'Erreur réseau Spotify.', retryable=True) from None


def _failure(response, *, token=False):
    status = response.status_code
    if status == 429:
        reason = None
        try:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get('error'), dict):
                reason = payload['error'].get('reason')
        except ValueError:
            pass
        code = 'quota_exceeded' if reason == 'QUOTA_EXCEEDED' else 'rate_limited'
        error = MetadataRetrievalError(code, 'Quota ou limite de requêtes Spotify atteint.', retryable=True)
        # Expose only a validated delay, never raw provider headers or bodies.
        delay = response.headers.get('Retry-After', '')
        error.retry_after_seconds = int(delay) if isinstance(delay, str) and re.fullmatch(r'[0-9]{1,8}', delay) else None
        raise error
    if status in (401, 403) or (token and status == 400):
        raise MetadataRetrievalError('access_denied', 'Accès Spotify refusé ; vérifier les autorisations et credentials.')
    if status == 404 and not token:
        raise MetadataRetrievalError('not_found', 'Morceau Spotify introuvable ou inaccessible.')
    if 500 <= status <= 599:
        raise MetadataRetrievalError('service_unavailable', 'Service Spotify indisponible.', retryable=True)
    raise MetadataRetrievalError('http_error', 'Échec de la requête Spotify.')


def _release(album):
    value = _text(album.get('release_date'))
    precision = _text(album.get('release_date_precision'))
    if value is None:
        return None, None
    patterns = {'year': r'[0-9]{4}', 'month': r'[0-9]{4}-[0-9]{2}',
                'day': r'[0-9]{4}-[0-9]{2}-[0-9]{2}'}
    if precision is None:
        # Validate date syntax without inventing the missing provider precision.
        shape = next((p for p, pattern in patterns.items() if re.fullmatch(pattern, value)), None)
        if shape is None:
            _invalid()
        try:
            date.fromisoformat(value + {'year': '-01-01', 'month': '-01', 'day': ''}[shape])
        except ValueError:
            _invalid()
        return value, None
    if precision not in patterns or not re.fullmatch(patterns[precision], value):
        _invalid()
    try:
        date.fromisoformat(value + {'year': '-01-01', 'month': '-01', 'day': ''}[precision])
    except ValueError:
        _invalid()
    return value, precision


def _artwork(album):
    images = album.get('images')
    if images is None:
        return None
    if not isinstance(images, list):
        _invalid()
    # Spotify documents images as widest first. Never request the returned URL.
    for image in images:
        if not isinstance(image, dict):
            _invalid()
        url = _text(image.get('url'))
        if url is None:
            continue
        try:
            parts = urlsplit(url)
            valid = (parts.scheme == 'https' and parts.hostname and parts.username is None
                     and parts.password is None and parts.port is None
                     and '\\' not in url and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url))
        except ValueError:
            valid = False
        if not valid:
            _invalid()
        return url
    return None


def _metadata(payload, source):
    if payload.get('id') != source.source_id:
        linked = payload.get('linked_from')
        if isinstance(linked, dict) and linked.get('id') == source.source_id:
            raise MetadataRetrievalError('source_relinked', 'Spotify propose une autre version pour ce marché.')
        _invalid()
    if payload.get('type', 'track') != 'track':
        _invalid()
    album = _object(payload.get('album'))
    artists = payload.get('artists')
    names = []
    if artists is not None:
        if not isinstance(artists, list):
            _invalid()
        for artist in artists:
            if not isinstance(artist, dict):
                _invalid()
            name = _text(artist.get('name'))
            if name:
                names.append(name)
    duration = payload.get('duration_ms')
    if duration is not None:
        if type(duration) is not int or duration < 0 or duration > 10**15:
            _invalid()
        duration = duration / 1000
    publication, precision = _release(album)
    return TrackMetadata(source=source, title=_text(payload.get('name')),
                         artist=', '.join(names) or None, duration_seconds=duration,
                         album=_text(album.get('name')), artwork_url=_artwork(album),
                         publication_date=publication, publication_date_precision=precision)


class SpotifyMetadataClient:
    def __init__(self):
        self._token = None
        self._expires_at = 0
        self._credential_fingerprint = None
        self._lock = threading.Lock()

    def _clear_token(self):
        self._token = None
        self._expires_at = 0
        self._credential_fingerprint = None

    def _get_token(self, client_id, secret):
        fingerprint = hashlib.sha256((client_id + '\0' + secret).encode()).digest()
        if self._credential_fingerprint != fingerprint:
            self._clear_token()
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token
        self._clear_token()
        started = time.monotonic()
        response = _request(requests.post, TOKEN_URL,
                            auth=(client_id, secret), data={'grant_type': 'client_credentials'},
                            headers={'Accept': 'application/json'})
        try:
            if response.status_code != 200:
                _failure(response, token=True)
            payload = _json(response)
            token = payload.get('access_token')
            ttl = payload.get('expires_in')
            if (not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token)
                    or type(ttl) is not int or not 0 < ttl <= 86400
                    or not isinstance(payload.get('token_type'), str)
                    or payload['token_type'].lower() != 'bearer'):
                _invalid()
            expiry = started + ttl - min(30, ttl / 10)
            if time.monotonic() >= expiry:
                raise MetadataRetrievalError('token_expired', 'Token Spotify déjà expiré.', retryable=True)
            self._token, self._expires_at = token, expiry
            self._credential_fingerprint = fingerprint
            return token
        finally:
            response.close()

    def fetch(self, source) -> TrackMetadata:
        """Read current environment on every call; disabled mode performs no I/O.

        A 401 invalidates the token and allows exactly one renewal/retry. Other
        errors never trigger retries, oEmbed, scraping, or requests to artwork.
        """
        with self._lock:
            mode = os.environ.get('SPOTIFY_METADATA_MODE', 'disabled')
            if mode != 'web_api':
                self._clear_token()
                if mode != 'disabled':
                    raise MetadataRetrievalError('invalid_configuration', 'Mode de métadonnées Spotify invalide.')
                raise MetadataRetrievalError('disabled', 'Récupération Spotify désactivée.')
            checked = normalize_source_url(source.source_url if isinstance(source, NormalizedSource) else source)
            if isinstance(source, NormalizedSource) and checked != source:
                raise MetadataURLValidationError('invalid_source', 'Identité source incohérente.')
            if checked.platform != 'spotify':
                raise MetadataURLValidationError('unsupported_platform', 'Un morceau Spotify est requis.')
            client_id = os.environ.get('SPOTIFY_CLIENT_ID', '').strip()
            secret = os.environ.get('SPOTIFY_CLIENT_SECRET', '').strip()
            market = os.environ.get('SPOTIFY_MARKET', '').strip().upper()
            if not client_id or not secret:
                self._clear_token()
                raise MetadataRetrievalError('missing_credentials', 'Credentials Spotify manquants.')
            if (not re.fullmatch('[A-Z]{2}', market) or ':' in client_id
                    or any(not 33 <= ord(c) <= 126 for c in client_id + secret)):
                self._clear_token()
                raise MetadataRetrievalError('invalid_configuration', 'Configuration Spotify invalide.')
            for attempt in range(2):
                token = self._get_token(client_id, secret)
                response = _request(requests.get, TRACK_URL + checked.source_id,
                                    params={'market': market},
                                    headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
                try:
                    if response.status_code == 401:
                        self._clear_token()
                        if attempt == 0:
                            continue
                    if response.status_code != 200:
                        _failure(response)
                    return _metadata(_json(response), checked)
                finally:
                    response.close()
