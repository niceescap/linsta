"""Contributor business service, independent of Flask and application config.

The caller MUST supply contributor_id from its trusted authentication context,
never directly from a request field. This service checks the current verified
database account and ownership; it cannot authenticate a session itself. Admins
may contribute on their own behalf, with no ownership bypass here.

Use an explicitly opened sqlite3.Row connection with foreign keys enabled and
no pending transaction. No initialization/migration or track creation occurs.
Provider fields are suggestions; only update_editorial() writes editorial data.
"""
from contextlib import contextmanager
import os

from metadata import MetadataRetrievalError, SpotifyMetadataClient, fetch_youtube_metadata, normalize_source_url
from metadata.persistence import (begin_metadata_attempt, get_metadata_attempts,
                                  save_metadata_failure, save_metadata_result)


_UNSET = object()


class SubmissionError(ValueError):
    """Stable business error, with no untrusted content in the message."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _fail(code, message):
    raise SubmissionError(code, message)


def _id(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        _fail('invalid_identifier', 'Un identifiant entier positif est requis.')


def _editorial_text(value):
    if value is None:
        return None
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail('invalid_editorial', 'Champ éditorial invalide.')
    return value.strip() or None


class SubmissionService:
    def __init__(self, conn):
        self.conn = conn
        self._spotify = SpotifyMetadataClient()

    def _ready(self):
        if self.conn.in_transaction:
            _fail('active_transaction', 'Une connexion sans transaction active est requise.')
        if self.conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            _fail('foreign_keys_disabled', 'Les clés étrangères doivent être activées.')

    @contextmanager
    def _transaction(self):
        self._ready()
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            yield

    def _actor(self, contributor_id):
        _id(contributor_id)
        actor = self.conn.execute('SELECT is_verified,role FROM users WHERE id=?', (contributor_id,)).fetchone()
        if actor is None or not actor['is_verified'] or actor['role'] not in ('contributor', 'admin'):
            _fail('unauthorized', 'Un compte contributeur vérifié est requis.')

    def _owned(self, contributor_id, submission_id):
        self._actor(contributor_id)
        _id(submission_id)
        row = self.conn.execute('SELECT * FROM submissions WHERE id=? AND contributor_id=?',
                                (submission_id, contributor_id)).fetchone()
        if row is None:
            # Deliberately indistinguishable from another contributor's record.
            _fail('not_found', 'Soumission introuvable.')
        return row

    def get_submission(self, contributor_id, submission_id):
        self._ready()
        row = self._owned(contributor_id, submission_id)
        result = dict(row)
        # Suggestions never overwrite editorial fields, even intentionally empty ones.
        result['editorial_suggestions'] = {'title': row['source_title'], 'artist': row['artist_name']}
        result['has_other_contributor_source'] = bool(self.conn.execute("""SELECT 1 FROM submissions
            WHERE platform=? AND source_id=? AND contributor_id!=? LIMIT 1""",
            (row['platform'], row['source_id'], contributor_id)).fetchone())
        return result

    def list_submissions(self, contributor_id, *, page=1, per_page=20):
        """Return only this contributor's rows, newest first, in bounded pages."""
        if type(page) is not int or not 1 <= page <= 2**31 - 1:
            _fail('invalid_page', 'Numéro de page invalide.')
        if type(per_page) is not int or not 1 <= per_page <= 100:
            _fail('invalid_page', 'Taille de page invalide.')
        self._ready()
        with self.conn:
            self.conn.execute('BEGIN')  # consistent count/list, no write lock or provider I/O
            self._actor(contributor_id)
            total = self.conn.execute('SELECT COUNT(*) FROM submissions WHERE contributor_id=?',
                                      (contributor_id,)).fetchone()[0]
            pages = max(1, (total + per_page - 1) // per_page)
            if page > pages:
                _fail('page_not_found', 'Page introuvable.')
            rows = self.conn.execute("""SELECT id,title,artist,source_title,platform,status,
                metadata_status,created_at FROM submissions WHERE contributor_id=?
                ORDER BY id DESC LIMIT ? OFFSET ?""",
                (contributor_id, per_page, (page - 1) * per_page)).fetchall()
            return {'items': [dict(row) for row in rows], 'page': page,
                    'pages': pages, 'per_page': per_page, 'total': total}

    def create_draft(self, contributor_id, source_url, *, fetch_metadata=True):
        """Commit the draft first; expected provider failures leave it editable.

        fetch_metadata=False explicitly defers retrieval (no attempt recorded).
        Cross-contributor sources are permitted and exposed as a boolean warning.
        """
        if type(fetch_metadata) is not bool:
            _fail('invalid_option', 'Option de récupération invalide.')
        with self._transaction():
            self._actor(contributor_id)
            source = normalize_source_url(source_url)
            duplicate = self.conn.execute("""SELECT 1 FROM submissions WHERE contributor_id=?
                AND platform=? AND source_id=? AND status!='rejected'""",
                (contributor_id, source.platform, source.source_id)).fetchone()
            if duplicate:
                _fail('duplicate_source', 'Une soumission active existe déjà pour cette source.')
            submission_id = self.conn.execute("""INSERT INTO submissions
                (contributor_id,platform,source_id,source_url,canonical_url)
                VALUES (?,?,?,?,?)""", (contributor_id, source.platform, source.source_id,
                                        source.source_url, source.canonical_url)).lastrowid
        if fetch_metadata:
            self.refresh_metadata(contributor_id, submission_id)
        return self.get_submission(contributor_id, submission_id)

    def refresh_metadata(self, contributor_id, submission_id):
        """Explicit retry, allowed for drafts/pending URL reviews only.

        The persistence layer owns its short transactions. No transaction spans
        a provider call. Unexpected programming/database failures propagate;
        a later explicit retry supersedes any unfinished attempt.
        """
        self._ready()
        row = self._owned(contributor_id, submission_id)
        if row['status'] not in ('draft', 'pending_review'):
            _fail('invalid_state', 'Récupération indisponible à cette étape de modération.')
        source = normalize_source_url(row['source_url'])
        attempt_id = begin_metadata_attempt(self.conn, submission_id)
        self._ready()
        try:
            if source.platform == 'youtube':
                metadata = fetch_youtube_metadata(source)
            else:
                mode = os.environ.get('SPOTIFY_METADATA_MODE', 'disabled')
                if mode == 'disabled':
                    raise MetadataRetrievalError('disabled', 'Récupération Spotify désactivée.')
                if mode != 'web_api':
                    raise MetadataRetrievalError('invalid_configuration', 'Mode Spotify invalide.')
                metadata = self._spotify.fetch(source)
        except MetadataRetrievalError as error:
            self._ready()
            self._owned(contributor_id, submission_id)
            save_metadata_failure(self.conn, attempt_id, error)
        else:
            self._ready()
            self._owned(contributor_id, submission_id)
            save_metadata_result(self.conn, attempt_id, metadata)
        return self.get_submission(contributor_id, submission_id)

    def update_editorial(self, contributor_id, submission_id, *, title=_UNSET, artist=_UNSET, genre_id=_UNSET):
        """Patch only a draft. Blank/None clears a field; submission then blocks
        until title, artist and a referenced genre are supplied again.
        """
        with self._transaction():
            row = self._owned(contributor_id, submission_id)
            if row['status'] != 'draft':
                _fail('invalid_state', 'Seul un brouillon peut être corrigé par son contributeur.')
            new_title = row['title'] if title is _UNSET else _editorial_text(title)
            new_artist = row['artist'] if artist is _UNSET else _editorial_text(artist)
            new_genre = row['genre_id'] if genre_id is _UNSET else genre_id
            if new_genre is not None:
                if type(new_genre) is not int or not 0 < new_genre <= 2**63 - 1 or not self.conn.execute(
                        'SELECT 1 FROM genres WHERE id=?', (new_genre,)).fetchone():
                    _fail('invalid_genre', 'Genre absent du référentiel.')
            self.conn.execute("""UPDATE submissions SET title=?,artist=?,genre_id=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""", (new_title, new_artist, new_genre, submission_id))
        return self.get_submission(contributor_id, submission_id)

    def submit_for_review(self, contributor_id, submission_id):
        """Only draft -> pending_review; metadata failure never implies rejection."""
        with self._transaction():
            row = self._owned(contributor_id, submission_id)
            if row['status'] != 'draft':
                _fail('invalid_state', 'Seul un brouillon peut être envoyé en revue.')
            title, artist = _editorial_text(row['title']), _editorial_text(row['artist'])
            if title is None or artist is None or row['genre_id'] is None:
                _fail('incomplete_editorial', 'Titre, artiste et genre sont obligatoires.')
            if not self.conn.execute('SELECT 1 FROM genres WHERE id=?', (row['genre_id'],)).fetchone():
                _fail('invalid_genre', 'Genre absent du référentiel.')
            self.conn.execute("""UPDATE submissions SET status='pending_review',title=?,artist=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='draft'""", (title, artist, submission_id))
        return self.get_submission(contributor_id, submission_id)

    def get_history(self, contributor_id, submission_id, *, limit=50):
        self._ready()
        self._owned(contributor_id, submission_id)
        return get_metadata_attempts(self.conn, submission_id, limit=limit)
