import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
from media_proof_fixtures import add_media_proof


ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite'

    def connect(self):
        conn = db.get_db(self.path)
        self.addCleanup(conn.close)
        return conn

    def initialize(self):
        db.init_db(self.path)
        conn = self.connect()
        conn.execute("INSERT INTO users(id,email,role) VALUES (1,'contributor@example.test','contributor')")
        conn.execute("INSERT INTO users(id,email,role) VALUES (2,'admin@example.test','admin')")
        conn.execute("INSERT INTO users(id,email) VALUES (3,'other@example.test')")
        conn.commit()
        return conn

    def submission(self, conn, owner=1, source='video1', platform='youtube'):
        return conn.execute("""INSERT INTO submissions
            (contributor_id,platform,source_id,source_url,canonical_url)
            VALUES (?,?,?,?,?)""", (owner, platform, source,
            'https://example.test/source', 'https://example.test/source')).lastrowid

    def pending(self, conn, sid):
        conn.execute("""UPDATE submissions SET status='pending_review',
            title='Title', artist='Artist', genre_id=1 WHERE id=?""", (sid,))

    def accepted(self, conn, sid):
        self.pending(conn, sid)
        conn.execute("""UPDATE submissions SET status='approved_awaiting_audio',
            reviewed_by=2, reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))

    def track(self, conn, status='pending', owner=1):
        return conn.execute("""INSERT INTO tracks
            (uploader_id,file_path,title,genre_id,status) VALUES (?, 'tracks/test.mp3','Legacy',1,?)""",
            (owner, status)).lastrowid

    def audio_pending(self, conn):
        sid = self.submission(conn)
        self.accepted(conn, sid)
        tid = self.track(conn)
        add_media_proof(conn, sid, tid)
        conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, sid))
        conn.commit()
        return sid, tid

    def states(self, conn, sid, tid):
        return (conn.execute('SELECT status FROM submissions WHERE id=?', (sid,)).fetchone()[0],
                conn.execute('SELECT status FROM tracks WHERE id=?', (tid,)).fetchone()[0])

    def migration_copy(self):
        directory = Path(self.temp.name) / 'migrations'
        directory.mkdir()
        (directory / '001_submissions.sql').write_bytes((ROOT / 'migrations/001_submissions.sql').read_bytes())
        return directory

    def test_fresh_initialization_and_repeat(self):
        db.init_db(self.path)
        db.init_db(self.path)
        conn = self.connect()
        self.assertEqual([r[0] for r in conn.execute('SELECT version FROM schema_migrations ORDER BY version')], [1, 2, 3])
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 3)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM genres').fetchone()[0], 3)
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0], 0)

    def test_legacy_migration_preserves_rows_and_schema(self):
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        conn.execute("INSERT INTO users(id,email) VALUES (1,'legacy@example.test')")
        for status in ('pending', 'approved', 'rejected'):
            self.track(conn, status)
        conn.execute("INSERT INTO plays(track_id,started_at) VALUES (2,'2026-01-01')")
        conn.commit()
        tables = ('users', 'genres', 'tracks', 'plays', 'invitations')
        before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
        columns = list(conn.execute('PRAGMA table_info(tracks)'))
        db.migrate_db(self.path)
        db.migrate_db(self.path)
        for table in tables:
            self.assertEqual(before[table], [tuple(r) for r in conn.execute(f'SELECT * FROM {table}')])
        self.assertEqual(columns, list(conn.execute('PRAGMA table_info(tracks)')))
        # Legacy moderation continues working without submission records.
        conn.execute("UPDATE tracks SET status='approved' WHERE id=1")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracks WHERE status='approved'").fetchone()[0], 2)

    def test_metadata_optional_but_editorial_fields_required_for_review(self):
        conn = self.initialize()
        sid = self.submission(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE submissions SET status='pending_review' WHERE id=?", (sid,))
        conn.execute("UPDATE submissions SET metadata_status='failed' WHERE id=?", (sid,))
        self.pending(conn, sid)
        for sql in (
            "UPDATE submissions SET status='bogus'",
            "UPDATE submissions SET platform='soundcloud'",
            "UPDATE submissions SET metadata_status='bogus'",
            "UPDATE submissions SET youtube_view_count=-1",
            "UPDATE submissions SET genre_id=999",
            "UPDATE submissions SET status='rejected'",
            "UPDATE submissions SET status='approved'",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.submission(conn, owner=999, source='missing-owner')
        spotify = self.submission(conn, source='spotify-track', platform='spotify')
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('UPDATE submissions SET youtube_view_count=0 WHERE id=?', (spotify,))

    def test_duplicates_rejected_terminal_and_new_attempt(self):
        conn = self.initialize()
        sid = self.submission(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            self.submission(conn)
        self.submission(conn, owner=3)  # Cross-contributor source is permitted.
        self.pending(conn, sid)
        conn.execute("""UPDATE submissions SET status='rejected', rejection_reason='Unsuitable',
            reviewed_by=2,reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE submissions SET status='pending_review' WHERE id=?", (sid,))
        self.submission(conn)  # New attempt; the rejected record remains.
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 3)

    def test_audio_requires_second_review(self):
        conn = self.initialize()
        sid = self.submission(conn)
        self.accepted(conn, sid)
        tid = self.track(conn)
        add_media_proof(conn, sid, tid)
        conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, sid))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE tracks SET status='approved' WHERE id=?", (tid,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE submissions SET status='approved' WHERE id=?", (sid,))
        conn.execute("""UPDATE submissions SET status='approved',audio_reviewed_by=2,
            audio_reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
        conn.execute("UPDATE tracks SET status='approved' WHERE id=?", (tid,))
        conn.execute("UPDATE tracks SET status='rejected' WHERE id=?", (tid,))
        conn.execute("UPDATE tracks SET status='approved' WHERE id=?", (tid,))
        self.assertEqual(conn.execute('SELECT status FROM tracks WHERE id=?', (tid,)).fetchone()[0], 'approved')

    def test_invalid_audio_links_and_audio_rejection(self):
        conn = self.initialize()
        sid = self.submission(conn)
        self.accepted(conn, sid)
        for tid in (999, self.track(conn, 'approved'), self.track(conn, owner=3)):
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, sid))
        tid = self.track(conn)
        add_media_proof(conn, sid, tid)
        conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, sid))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE submissions SET status='rejected',rejection_reason='Wrong audio' WHERE id=?", (sid,))
        conn.execute("""UPDATE submissions SET status='rejected',rejection_reason='Wrong audio',
            audio_reviewed_by=2,audio_reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE tracks SET status='approved' WHERE id=?", (tid,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('UPDATE submissions SET track_id=NULL WHERE id=?', (sid,))

    def test_admin_review_and_one_track_per_submission(self):
        conn = self.initialize()
        sid = self.submission(conn)
        self.pending(conn, sid)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""UPDATE submissions SET status='approved_awaiting_audio',
                reviewed_by=1,reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
        self.accepted(conn, sid)
        tid = self.track(conn)
        add_media_proof(conn, sid, tid)
        conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, sid))
        other = self.submission(conn, source='another-video')
        self.accepted(conn, other)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, other))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""UPDATE submissions SET status='approved',audio_reviewed_by=1,
                audio_reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))

    def test_failed_migration_rolls_back_schema_and_ledger(self):
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        conn.execute('CREATE TABLE submissions(existing_column TEXT)')
        conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            db.migrate_db(self.path)
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())
        self.assertEqual(conn.execute('PRAGMA table_info(submissions)').fetchone()['name'], 'existing_column')

    def test_read_connection_does_not_migrate(self):
        conn = self.connect()
        self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_premature_review_fields_insert_and_update(self):
        conn = self.initialize()
        for field, value in (('reviewed_by', 1), ('reviewed_by', 2), ('reviewed_at', 'now'),
                             ('audio_reviewed_by', 1), ('audio_reviewed_by', 2), ('audio_reviewed_at', 'now')):
            with self.subTest(field=field, value=value), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f"""INSERT INTO submissions(contributor_id,platform,source_id,
                    source_url,canonical_url,{field}) VALUES (1,'youtube','insert','url','url',?)""", (value,))
        sid = self.submission(conn)
        for stage in ('draft', 'pending_review', 'approved_awaiting_audio'):
            if stage == 'pending_review':
                self.pending(conn, sid)
            elif stage == 'approved_awaiting_audio':
                conn.execute("""UPDATE submissions SET status='approved_awaiting_audio',
                    reviewed_by=2, reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
            for assignment in ('audio_reviewed_by=2', "audio_reviewed_at='now'",
                               "audio_reviewed_by=2,audio_reviewed_at='now'"):
                with self.subTest(stage=stage, assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f'UPDATE submissions SET {assignment} WHERE id=?', (sid,))

    def test_audio_identity_immutable_before_and_after_approval(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        for approved in (False, True):
            if approved:
                db.finalize_audio_review(conn, sid, 2, approve=True)
            for assignment in ("file_path='tracks/replacement.mp3'", 'uploader_id=3', 'id=999',
                               'file_size=100', "mime_type='audio/other'", "original_filename='other.mp3'",
                               'duration_seconds=12'):
                with self.subTest(approved=approved, assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(f'UPDATE tracks SET {assignment} WHERE id=?', (tid,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT OR REPLACE INTO tracks(id,uploader_id,file_path,title,genre_id,status)
                    VALUES (?,1,'tracks/replacement.mp3','Other',1,'approved')""", (tid,))
            conn.rollback()

    def test_demoted_historical_reviewer_does_not_block_operations(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        conn.execute("UPDATE users SET role='contributor' WHERE id=2")
        conn.execute("UPDATE users SET role='admin' WHERE id=3")
        conn.execute("UPDATE submissions SET metadata_status='partial' WHERE id=?", (sid,))
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            db.finalize_audio_review(conn, sid, 2, approve=True)
        db.finalize_audio_review(conn, sid, 3, approve=True)
        conn.execute("UPDATE users SET role='contributor' WHERE id=3")
        conn.execute("UPDATE submissions SET metadata_status='available' WHERE id=?", (sid,))
        self.assertEqual(self.states(conn, sid, tid), ('approved', 'approved'))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('UPDATE submissions SET audio_reviewed_by=1 WHERE id=?', (sid,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('UPDATE submissions SET reviewed_by=1 WHERE id=?', (sid,))

    def test_final_approval_commit_visible_to_second_connection(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        observer = self.connect()
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("""UPDATE submissions SET status='approved',audio_reviewed_by=2,
            audio_reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
        self.assertEqual(self.states(observer, sid, tid), ('pending_audio_review', 'pending'))
        conn.execute("UPDATE tracks SET status='approved' WHERE id=?", (tid,))
        self.assertEqual(self.states(observer, sid, tid), ('pending_audio_review', 'pending'))
        conn.commit()
        self.assertEqual(self.states(observer, sid, tid), ('approved', 'approved'))

    def test_final_approval_failure_rolls_back_both_rows(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        observer = self.connect()
        # Force step 2 to fail after the submission update has succeeded.
        conn.execute("""CREATE TRIGGER simulate_failure BEFORE UPDATE OF status ON tracks
            WHEN NEW.status='approved' BEGIN SELECT RAISE(ABORT,'test failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            db.finalize_audio_review(conn, sid, 2, approve=True)
        self.assertFalse(conn.in_transaction)
        self.assertEqual(self.states(observer, sid, tid), ('pending_audio_review', 'pending'))
        self.assertIsNone(observer.execute('SELECT audio_reviewed_by FROM submissions WHERE id=?', (sid,)).fetchone()[0])

    def test_track_first_approval_failure_has_no_partial_state(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        observer = self.connect()
        with self.assertRaises(sqlite3.IntegrityError), conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE tracks SET status='approved' WHERE id=?", (tid,))
        self.assertEqual(self.states(observer, sid, tid), ('pending_audio_review', 'pending'))

    def test_final_rejection_coherent_and_history_preserved(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        observer = self.connect()
        with self.assertRaises(sqlite3.IntegrityError):
            db.finalize_audio_review(conn, sid, 1, approve=False, reason='Wrong recording')
        db.finalize_audio_review(conn, sid, 2, approve=False, reason='Wrong recording')
        self.assertEqual(self.states(observer, sid, tid), ('rejected', 'rejected'))
        submission = observer.execute('SELECT * FROM submissions WHERE id=?', (sid,)).fetchone()
        track = observer.execute('SELECT * FROM tracks WHERE id=?', (tid,)).fetchone()
        self.assertEqual(track['rejection_reason'], submission['rejection_reason'])
        self.assertEqual(track['reviewed_by'], submission['audio_reviewed_by'])
        self.assertEqual(track['reviewed_at'], submission['audio_reviewed_at'])
        self.submission(conn)  # Fresh attempt, rejected attempt retained.
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE tracks SET status='pending' WHERE id=?", (tid,))

    def test_rejection_failure_rolls_back_both_rows(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        conn.execute("""CREATE TRIGGER simulate_failure BEFORE UPDATE OF status ON tracks
            WHEN NEW.status='rejected' BEGIN SELECT RAISE(ABORT,'test failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            db.finalize_audio_review(conn, sid, 2, approve=False, reason='Wrong audio')
        self.assertEqual(self.states(self.connect(), sid, tid), ('pending_audio_review', 'pending'))

    def test_deletion_and_foreign_keys(self):
        conn = self.initialize()
        draft = self.submission(conn, source='discard')
        conn.execute('DELETE FROM submissions WHERE id=?', (draft,))
        sid = self.submission(conn)
        self.pending(conn, sid)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM submissions WHERE id=?', (sid,))
        self.accepted(conn, sid)
        tid = self.track(conn)
        add_media_proof(conn, sid, tid)
        conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?", (tid, sid))
        conn.commit()
        for status in ('pending_audio_review', 'rejected'):
            if status == 'rejected':
                db.finalize_audio_review(conn, sid, 2, approve=False, reason='No')
            for sql, value in (('DELETE FROM submissions WHERE id=?', sid),
                               ('DELETE FROM tracks WHERE id=?', tid),
                               ('DELETE FROM users WHERE id=?', 1),
                               ('DELETE FROM users WHERE id=?', 2)):
                with self.subTest(status=status, sql=sql), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(sql, (value,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT OR REPLACE INTO submissions(id,contributor_id,platform,source_id,
                    source_url,canonical_url) VALUES (?,1,'youtube','video1','url','url')""", (sid,))
            conn.rollback()
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_numeric_migration_order_and_duplicates(self):
        directory = self.migration_copy()
        (directory / '2_second.sql').write_text('CREATE TABLE sequence_test(value INTEGER);\nINSERT INTO sequence_test VALUES (2);\n')
        (directory / '10_tenth.sql').write_text('INSERT INTO sequence_test VALUES (10);\n')
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        db.migrate_db(self.path, migrations_dir=directory)
        self.assertEqual([r[0] for r in conn.execute('SELECT value FROM sequence_test')], [2, 10])
        (directory / '002_duplicate.sql').write_text('CREATE TABLE should_not_exist(id);\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            db.migrate_db(self.path, migrations_dir=directory)

    def test_changed_applied_migration_is_rejected(self):
        directory = self.migration_copy()
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        db.migrate_db(self.path, migrations_dir=directory)
        file = directory / '001_submissions.sql'
        file.write_text(file.read_text() + '\n-- changed\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            db.migrate_db(self.path, migrations_dir=directory)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 1)

    def test_missing_resources_and_applied_names(self):
        directory = self.migration_copy()
        with self.assertRaisesRegex(ValueError, 'missing'):
            db.migrate_db(self.path, migrations_dir=directory / 'absent')
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        db.migrate_db(self.path, migrations_dir=directory)
        conn.execute("UPDATE schema_migrations SET name='001_wrong.sql'")
        conn.commit()
        with self.assertRaisesRegex(ValueError, 'changed'):
            db.migrate_db(self.path, migrations_dir=directory)

    def test_mid_migration_failure_rolls_back_and_retries(self):
        directory = self.migration_copy()
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        original = (directory / '001_submissions.sql').read_text()
        (directory / '001_submissions.sql').write_text(original + '\nINSERT INTO nonexistent_table VALUES (1);\n')
        with self.assertRaises(sqlite3.OperationalError):
            db.migrate_db(self.path, migrations_dir=directory)
        names = {r[0] for r in conn.execute('SELECT name FROM sqlite_master')}
        self.assertNotIn('submissions', names)
        self.assertNotIn('schema_migrations', names)
        self.assertNotIn('submitted_track_approval', names)
        (directory / '001_submissions.sql').write_text(original)
        db.migrate_db(self.path, migrations_dir=directory)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0], 1)

    def test_complete_legacy_moderation_and_selection(self):
        conn = self.initialize()
        tid = self.track(conn)
        for status in ('approved', 'rejected', 'approved', 'pending', 'rejected'):
            conn.execute('UPDATE tracks SET status=? WHERE id=?', (status, tid))
            selected = conn.execute("SELECT t.id FROM tracks t JOIN genres g ON g.id=t.genre_id WHERE t.status='approved'").fetchall()
            self.assertEqual([r[0] for r in selected], [tid] if status == 'approved' else [])
        conn.execute('DELETE FROM tracks WHERE id=?', (tid,))

    def test_review_fields_only_change_on_decision_and_then_freeze(self):
        conn = self.initialize()
        sid, tid = self.audio_pending(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""UPDATE submissions SET audio_reviewed_by=2,
                audio_reviewed_at=CURRENT_TIMESTAMP WHERE id=?""", (sid,))
        conn.rollback()
        db.finalize_audio_review(conn, sid, 2, approve=True)
        for assignment in ('audio_reviewed_by=NULL', "audio_reviewed_at='different'",
                           "reviewed_at='different'", "rejection_reason='invented'"):
            with self.subTest(assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f'UPDATE submissions SET {assignment} WHERE id=?', (sid,))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM submissions WHERE id=?', (sid,))

    def test_missing_applied_resource_and_retroactive_version(self):
        directory = self.migration_copy()
        later = directory / '10_later.sql'
        later.write_text('CREATE TABLE later_version(id INTEGER);\n')
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        db.migrate_db(self.path, migrations_dir=directory)
        later.rename(directory / '10_renamed.sql')
        with self.assertRaisesRegex(ValueError, 'changed'):
            db.migrate_db(self.path, migrations_dir=directory)
        (directory / '10_renamed.sql').rename(later)
        # Supply an inventory that omits the applied higher version.
        subset = Path(self.temp.name) / 'subset'
        subset.mkdir()
        (subset / '001_submissions.sql').write_bytes((directory / '001_submissions.sql').read_bytes())
        with self.assertRaisesRegex(ValueError, 'missing'):
            db.migrate_db(self.path, migrations_dir=subset)
        (directory / '2_retroactive.sql').write_text('CREATE TABLE retroactive(id);\n')
        with self.assertRaisesRegex(ValueError, 'Out-of-order'):
            db.migrate_db(self.path, migrations_dir=directory)
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='retroactive'").fetchone())

    def test_old_unverified_ledger_fails_closed(self):
        conn = self.connect()
        conn.executescript((ROOT / 'schema.sql').read_text())
        conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT)')
        conn.commit()
        with self.assertRaisesRegex(ValueError, 'checksum missing'):
            db.migrate_db(self.path)


if __name__ == '__main__':
    unittest.main()
