"""SQLite-only metadata persistence. No Flask, HTTP, config or file operations.

Writes require an idle sqlite3.Row connection with foreign keys enabled, e.g.
db.get_db(explicit_path). Each function owns a short transaction. The caller
authorizes access and performs provider calls strictly between transactions.
Only provider snapshot/attempt fields are written, never editorial/moderation
fields, tracks or artwork files. Spotify activation remains the adapter's job.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import math
import re
from urllib.parse import urlsplit

from .errors import MetadataRetrievalError
from .models import NormalizedSource, TrackMetadata
from .urls import normalize_source_url


ERROR_CODES = frozenset((
    'disabled', 'invalid_configuration', 'missing_credentials', 'missing_api_key',
    'access_denied', 'not_found', 'quota_exceeded', 'rate_limited', 'timeout',
    'network_error', 'service_unavailable', 'http_error', 'invalid_response',
    'source_relinked', 'token_expired',
))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


@contextmanager
def _transaction(conn):
    if conn.in_transaction:
        raise ValueError('Metadata writes require an idle connection')
    if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Foreign keys must be enabled')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        yield


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Positive integer identifier required')


def _identity(row):
    source = normalize_source_url(row['source_url'])
    if (source.platform, source.source_id, source.canonical_url) != (
            row['platform'], row['source_id'], row['canonical_url']):
        raise ValueError('Stored provider identity is inconsistent')
    return source


def _attempt(conn, attempt_id):
    row = conn.execute('SELECT * FROM submission_metadata_attempts WHERE id=?', (attempt_id,)).fetchone()
    if row is None:
        raise ValueError('Metadata attempt not found')
    submission = conn.execute('SELECT * FROM submissions WHERE id=?', (row['submission_id'],)).fetchone()
    _identity(submission)
    return row, submission


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError('Invalid metadata text')
    return value.strip() or None


def _date_pair(value, precision):
    value = _text(value)
    if value is None:
        if precision is not None:
            raise ValueError('Date precision requires a date')
        return None, None
    patterns = {'year': r'[0-9]{4}', 'month': r'[0-9]{4}-[0-9]{2}',
                'day': r'[0-9]{4}-[0-9]{2}-[0-9]{2}',
                'second': r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                          r'(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})'}
    if precision is not None and (not isinstance(precision, str) or precision not in patterns):
        raise ValueError('Invalid date precision')
    shape = precision or next((p for p, pattern in patterns.items() if re.fullmatch(pattern, value)), None)
    if shape is None or not re.fullmatch(patterns[shape], value):
        raise ValueError('Invalid publication date')
    if shape == 'second':
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        date.fromisoformat(value + {'year': '-01-01', 'month': '-01', 'day': ''}[shape])
    return value, precision


def _values(metadata):
    if not isinstance(metadata, TrackMetadata) or not isinstance(metadata.source, NormalizedSource):
        raise ValueError('TrackMetadata with normalized source required')
    source = normalize_source_url(metadata.source.source_url)
    if source != metadata.source:
        raise ValueError('Provider identity is inconsistent')
    duration = metadata.duration_seconds
    if duration is not None:
        if type(duration) not in (float, int):
            raise ValueError('Invalid provider duration')
        try:
            valid = math.isfinite(duration) and duration >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('Invalid provider duration')
        duration = float(duration)  # REAL binding also handles integers beyond SQLite int64.
    views = metadata.youtube_view_count
    if views is not None and (source.platform != 'youtube' or type(views) is not int
                              or not 0 <= views <= 2**63 - 1):
        raise ValueError('Invalid YouTube view count')
    artwork = _text(metadata.artwork_url)
    if artwork is not None:
        parsed = urlsplit(artwork)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port is not None or '\\' in artwork
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in artwork)):
            raise ValueError('Invalid artwork URL')
    publication, precision = _date_pair(metadata.publication_date, metadata.publication_date_precision)
    return {'source_title': _text(metadata.title), 'artist_name': _text(metadata.artist),
            'channel_name': _text(metadata.channel), 'source_duration_seconds': duration,
            'source_album': _text(metadata.album), 'source_description': _text(metadata.description),
            'artwork_url': artwork, 'publication_date': publication,
            'publication_date_precision': precision, 'youtube_view_count': views}


def begin_metadata_attempt(conn, submission_id):
    """Start/retry explicitly; supersede unfinished older attempts. Return ID."""
    _positive_id(submission_id)
    with _transaction(conn):
        submission = conn.execute('SELECT * FROM submissions WHERE id=?', (submission_id,)).fetchone()
        if submission is None or submission['status'] == 'rejected':
            raise ValueError('Submission missing or terminally rejected')
        _identity(submission)
        now = _now()
        conn.execute("""UPDATE submission_metadata_attempts SET status='superseded',finished_at=?
            WHERE submission_id=? AND status='pending'""", (now, submission_id))
        generation = submission['metadata_generation'] + 1
        conn.execute("""UPDATE submissions SET metadata_generation=?,metadata_status='pending',updated_at=?
            WHERE id=?""", (generation, now, submission_id))
        return conn.execute("""INSERT INTO submission_metadata_attempts(submission_id,generation,started_at)
            VALUES (?,?,?)""", (submission_id, generation, now)).lastrowid


def _superseded(conn, attempt, submission, now):
    if attempt['status'] == 'superseded':
        return True
    if attempt['status'] != 'pending':
        raise ValueError('Metadata attempt already completed')
    if attempt['generation'] != submission['metadata_generation'] or submission['status'] == 'rejected':
        conn.execute("UPDATE submission_metadata_attempts SET status='superseded',finished_at=? WHERE id=?",
                     (now, attempt['id']))
        return True
    return False


def save_metadata_result(conn, attempt_id, metadata):
    """Merge nonempty provider fields; never write editorial fields.

    Status describes this response, not completeness of the retained snapshot.
    available requires title/duration/artwork and channel (YouTube) or artist
    (Spotify). Publication, album, description, views and YouTube artist are
    optional. Even an entirely empty valid response is partial, never rejected.
    """
    _positive_id(attempt_id)
    values = _values(metadata)
    with _transaction(conn):
        attempt, submission = _attempt(conn, attempt_id)
        if metadata.source != _identity(submission):
            raise ValueError('Metadata does not match submission identity')
        now = _now()
        if _superseded(conn, attempt, submission, now):
            return 'superseded'
        required = ('source_title', 'source_duration_seconds', 'artwork_url',
                    'channel_name' if submission['platform'] == 'youtube' else 'artist_name')
        status = 'available' if all(values[k] is not None for k in required) else 'partial'
        # Date and precision form one observation. Never combine a new date
        # with stale precision, or degrade the precision of the same date.
        if values['publication_date'] is None or (
                values['publication_date'] == submission['publication_date']
                and values['publication_date_precision'] is None):
            values['publication_date'] = submission['publication_date']
            values['publication_date_precision'] = submission['publication_date_precision']
        merged = {key: value if value is not None else submission[key] for key, value in values.items()
                  if key not in ('publication_date', 'publication_date_precision')}
        merged.update({key: values[key] for key in ('publication_date', 'publication_date_precision')})
        assignments = ','.join(f'{key}=?' for key in merged)  # fixed internal keys only
        conn.execute(f"""UPDATE submissions SET {assignments},metadata_status=?,metadata_error_code=NULL,
            metadata_fetched_at=?,updated_at=? WHERE id=?""",
            (*merged.values(), status, now, now, submission['id']))
        conn.execute("""UPDATE submission_metadata_attempts SET status=?,finished_at=? WHERE id=?""",
                     (status, now, attempt_id))
        return status


def save_metadata_failure(conn, attempt_id, error):
    """Persist only an allowlisted code and retry hint, never exception text."""
    _positive_id(attempt_id)
    if (not isinstance(error, MetadataRetrievalError) or not isinstance(error.code, str)
            or error.code not in ERROR_CODES or type(error.retryable) is not bool):
        raise ValueError('Unsupported metadata error')
    delay = getattr(error, 'retry_after_seconds', None)
    if delay is not None and (type(delay) is not int or not 0 <= delay <= 99999999 or not error.retryable):
        raise ValueError('Invalid retry delay')
    with _transaction(conn):
        attempt, submission = _attempt(conn, attempt_id)
        now = _now()
        if _superseded(conn, attempt, submission, now):
            return 'superseded'
        retry_at = (datetime.fromisoformat(now) + timedelta(seconds=delay)).isoformat() if delay is not None else None
        conn.execute("""UPDATE submission_metadata_attempts SET status='failed',finished_at=?,
            error_code=?,retryable=?,retry_after_at=? WHERE id=?""",
            (now, error.code, int(error.retryable), retry_at, attempt_id))
        conn.execute("""UPDATE submissions SET metadata_status='failed',metadata_error_code=?,updated_at=?
            WHERE id=?""", (error.code, now, submission['id']))
        return 'failed'


def get_metadata_attempts(conn, submission_id, *, limit=50):
    """Read newest first, bounded to 200 rows; authorization belongs to caller."""
    _positive_id(submission_id)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('History limit must be between 1 and 200')
    return conn.execute("""SELECT * FROM submission_metadata_attempts
        WHERE submission_id=? ORDER BY id DESC LIMIT ?""", (submission_id, limit)).fetchall()
