"""URL review business service. No Flask, provider calls, audio or track writes.

Pass the authenticated actor ID, never a form-supplied reviewer. Current verified
admin rights are checked inside each transaction. BEGIN IMMEDIATE serializes
writers: repeated/concurrent decisions fail after the first committed decision.
"""
from contextlib import contextmanager

from submissions import SubmissionError, _editorial_text, _id, _UNSET


STATUSES = ('draft', 'pending_review', 'approved_awaiting_audio',
            'pending_audio_review', 'approved', 'rejected')
PLATFORMS = ('youtube', 'spotify')


def _fail(code, message):
    raise SubmissionError(code, message)


class SubmissionReviewService:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def _transaction(self, admin_id, *, write=False):
        if self.conn.in_transaction:
            _fail('active_transaction', 'Une connexion sans transaction active est requise.')
        if self.conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            _fail('foreign_keys_disabled', 'Les clés étrangères doivent être activées.')
        _id(admin_id)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            actor = self.conn.execute('SELECT role,is_verified FROM users WHERE id=?', (admin_id,)).fetchone()
            if actor is None or actor['role'] != 'admin' or not actor['is_verified']:
                _fail('unauthorized', 'Un compte administrateur vérifié est requis.')
            yield

    def _row(self, submission_id):
        _id(submission_id)
        row = self.conn.execute('SELECT * FROM submissions WHERE id=?', (submission_id,)).fetchone()
        if row is None:
            _fail('not_found', 'Proposition introuvable.')
        return row

    def _pending(self, submission_id):
        row = self._row(submission_id)
        if row['status'] != 'pending_review':
            _fail('invalid_state', 'Cette proposition ne peut plus faire l’objet d’une décision URL.')
        return row

    def _genre(self, genre_id):
        if genre_id is not None:
            _id(genre_id)
            if not self.conn.execute('SELECT 1 FROM genres WHERE id=?', (genre_id,)).fetchone():
                _fail('invalid_genre', 'Genre absent du référentiel.')

    def _duplicates(self, row):
        # Include rejected history; another contributor's source is a warning,
        # never an automatic rejection. Only administrators receive this data.
        return [dict(r) for r in self.conn.execute('''SELECT id,contributor_id,status
            FROM submissions WHERE platform=? AND source_id=? AND contributor_id!=?
            ORDER BY id DESC''', (row['platform'], row['source_id'], row['contributor_id']))]

    def list_submissions(self, admin_id, *, status='pending_review', platform=None, page=1, per_page=20):
        if status is not None and status not in STATUSES:
            _fail('invalid_filter', 'Filtre de statut invalide.')
        if platform is not None and platform not in PLATFORMS:
            _fail('invalid_filter', 'Filtre de plateforme invalide.')
        if (type(page) is not int or not 1 <= page <= 2**31-1
                or type(per_page) is not int or not 1 <= per_page <= 100):
            _fail('invalid_page', 'Pagination invalide.')
        with self._transaction(admin_id):
            clauses, params = [], []
            if status:
                clauses.append('s.status=?')
                params.append(status)
            if platform:
                clauses.append('s.platform=?')
                params.append(platform)
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            total = self.conn.execute('SELECT COUNT(*) FROM submissions s' + where, params).fetchone()[0]
            pages = max(1, (total+per_page-1)//per_page)
            if page > pages:
                _fail('page_not_found', 'Page introuvable.')
            rows = self.conn.execute('''SELECT s.id,s.title,s.artist,s.source_title,s.platform,
                s.status,s.created_at, EXISTS(SELECT 1 FROM submissions d
                WHERE d.platform=s.platform AND d.source_id=s.source_id
                AND d.contributor_id!=s.contributor_id) AS has_duplicate
                FROM submissions s''' + where + ' ORDER BY s.id ASC LIMIT ? OFFSET ?',
                params + [per_page, (page-1)*per_page]).fetchall()
            return {'items': [dict(r) for r in rows], 'page': page, 'pages': pages, 'total': total}

    def get_submission(self, admin_id, submission_id):
        with self._transaction(admin_id):
            row = self._row(submission_id)
            result = dict(row)
            result['duplicates'] = self._duplicates(row)
            return result

    def update_editorial(self, admin_id, submission_id, *, title=_UNSET, artist=_UNSET, genre_id=_UNSET):
        with self._transaction(admin_id, write=True):
            row = self._pending(submission_id)
            title = _editorial_text(row['title'] if title is _UNSET else title)
            artist = _editorial_text(row['artist'] if artist is _UNSET else artist)
            genre_id = row['genre_id'] if genre_id is _UNSET else genre_id
            if not title or not artist or genre_id is None:
                _fail('incomplete_editorial', 'Titre, artiste et genre sont obligatoires.')
            self._genre(genre_id)
            self.conn.execute('''UPDATE submissions SET title=?,artist=?,genre_id=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending_review' ''',
                (title, artist, genre_id, submission_id))
        return submission_id

    def _decide(self, admin_id, submission_id, *, accept, reason=None):
        with self._transaction(admin_id, write=True):
            row = self._pending(submission_id)
            if accept:
                title, artist = _editorial_text(row['title']), _editorial_text(row['artist'])
                if not title or not artist or row['genre_id'] is None:
                    _fail('incomplete_editorial', 'Titre, artiste et genre sont obligatoires.')
                self._genre(row['genre_id'])
            else:
                reason = _editorial_text(reason)
                if not reason:
                    _fail('missing_reason', 'Un motif de refus est obligatoire.')
            changed = self.conn.execute('''UPDATE submissions SET status=?, reviewed_by=?,
                reviewed_at=CURRENT_TIMESTAMP,rejection_reason=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending_review' ''',
                ('approved_awaiting_audio' if accept else 'rejected', admin_id,
                 None if accept else reason, submission_id))
            if changed.rowcount != 1:
                _fail('invalid_state', 'La proposition a déjà été traitée.')
        return submission_id

    def accept(self, admin_id, submission_id):
        return self._decide(admin_id, submission_id, accept=True)

    def reject(self, admin_id, submission_id, *, reason):
        return self._decide(admin_id, submission_id, accept=False, reason=reason)
