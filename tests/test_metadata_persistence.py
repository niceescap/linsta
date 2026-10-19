import hashlib
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import db
from media_proof_fixtures import add_media_proof
from metadata import MetadataRetrievalError, SpotifyMetadataClient, TrackMetadata, normalize_source_url
from metadata.persistence import (begin_metadata_attempt, get_metadata_attempts,
                                  save_metadata_failure, save_metadata_result)


ROOT = Path(__file__).resolve().parents[1]
URL = 'https://youtu.be/dQw4w9WgXcQ'


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'metadata.sqlite'
        db.init_db(self.path)
        self.conn = self.connect()
        self.conn.execute("INSERT INTO users(id,email) VALUES (1,'one@example.test')")
        self.conn.execute("INSERT INTO users(id,email,role) VALUES (2,'admin@example.test','admin')")
        self.conn.execute("INSERT INTO users(id,email) VALUES (3,'three@example.test')")
        self.sid = self.submission()
        self.conn.commit()
        self.source = normalize_source_url(URL)
        self.result = TrackMetadata(self.source, title='Source title', artist=None, channel='Channel',
            duration_seconds=120.5, artwork_url='https://i.ytimg.com/fake.jpg', album='Album',
            description='Description', publication_date='2020-02-29T12:00:00Z',
            publication_date_precision='second', youtube_view_count=0)
        socket = patch('socket.socket', side_effect=AssertionError('No network'))
        socket.start()
        self.addCleanup(socket.stop)

    def connect(self):
        conn = db.get_db(self.path)
        self.addCleanup(conn.close)
        return conn

    def submission(self, url=URL, owner=1):
        source = normalize_source_url(url)
        return self.conn.execute("""INSERT INTO submissions
            (contributor_id,platform,source_id,source_url,canonical_url)
            VALUES (?,?,?,?,?)""", (owner, source.platform, source.source_id,
                                    source.source_url, source.canonical_url)).lastrowid

    def row(self, conn=None):
        return (conn or self.conn).execute('SELECT * FROM submissions WHERE id=?', (self.sid,)).fetchone()

    def reject(self):
        self.conn.execute("""UPDATE submissions SET status='pending_review',
            title='Editorial title',artist='Editorial artist',genre_id=1 WHERE id=?""", (self.sid,))
        self.conn.execute("""UPDATE submissions SET status='rejected',reviewed_by=2,
            reviewed_at=CURRENT_TIMESTAMP,rejection_reason='Not suitable' WHERE id=?""", (self.sid,))
        self.conn.commit()

    def test_begin_and_result_visible_on_second_connection(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        observer = self.connect()
        self.assertEqual(self.row(observer)['metadata_generation'], 1)
        self.assertEqual(save_metadata_result(self.conn, attempt, self.result), 'available')
        row = self.row(observer)
        self.assertEqual(row['source_title'], 'Source title')
        self.assertEqual(row['channel_name'], 'Channel')
        self.assertIsNone(row['artist_name'])
        self.assertEqual(row['source_duration_seconds'], 120.5)
        self.assertEqual(row['source_album'], 'Album')
        self.assertEqual(row['source_description'], 'Description')
        self.assertEqual(row['youtube_view_count'], 0)
        self.assertIsNotNone(row['metadata_fetched_at'])
        self.assertEqual(get_metadata_attempts(observer, self.sid)[0]['status'], 'available')
        self.assertFalse(self.conn.in_transaction)

    def test_no_tracks_editorial_or_moderation_changes(self):
        self.conn.execute("UPDATE submissions SET title='Correction',artist='Verified artist',genre_id=2 WHERE id=?", (self.sid,))
        self.conn.commit()
        before = dict(self.row())
        attempt = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, attempt, replace(self.result, artist='Provider artist'))
        for key in ('title','artist','genre_id','status','track_id','reviewed_by','reviewed_at',
                    'audio_reviewed_by','audio_reviewed_at','rejection_reason','source_url','canonical_url'):
            self.assertEqual(self.row()[key], before[key], key)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM plays').fetchone()[0], 0)

    def test_partial_preserves_useful_fields_and_zero(self):
        first = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, first, self.result)
        second = begin_metadata_attempt(self.conn, self.sid)
        self.assertEqual(save_metadata_result(self.conn, second,
                         TrackMetadata(self.source, title='New title', duration_seconds=0)), 'partial')
        row = self.row()
        self.assertEqual(row['source_title'], 'New title')
        self.assertEqual(row['source_duration_seconds'], 0)
        self.assertEqual(row['source_album'], 'Album')
        self.assertEqual(row['artwork_url'], self.result.artwork_url)
        self.assertEqual(row['publication_date'], self.result.publication_date)
        self.assertEqual(row['publication_date_precision'], 'second')

    def test_empty_response_is_partial(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        self.assertEqual(save_metadata_result(self.conn, attempt, TrackMetadata(self.source)), 'partial')
        self.assertEqual(self.row()['status'], 'draft')

    def test_failure_retry_and_history(self):
        first = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, first, self.result)
        fetched_at = self.row()['metadata_fetched_at']
        second = begin_metadata_attempt(self.conn, self.sid)
        error = MetadataRetrievalError('rate_limited', 'SECRET MUST NOT BE STORED', retryable=True)
        error.retry_after_seconds = 60
        self.assertEqual(save_metadata_failure(self.conn, second, error), 'failed')
        row = self.row()
        self.assertEqual(row['source_title'], self.result.title)
        self.assertEqual(row['metadata_fetched_at'], fetched_at)
        self.assertEqual(row['metadata_error_code'], 'rate_limited')
        attempts = get_metadata_attempts(self.conn, self.sid)
        self.assertEqual([r['status'] for r in attempts], ['failed','available'])
        self.assertIsNotNone(attempts[0]['retry_after_at'])
        self.assertNotIn('SECRET MUST NOT BE STORED', str([dict(r) for r in attempts]))
        third = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, third, self.result)
        self.assertIsNone(self.row()['metadata_error_code'])
        self.assertEqual(len(get_metadata_attempts(self.conn, self.sid)), 3)

    def test_stale_success_and_failure_cannot_overwrite_new_result(self):
        old = begin_metadata_attempt(self.conn, self.sid)
        current = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, current, replace(self.result, title='Newest'))
        before = dict(self.row())
        self.assertEqual(save_metadata_result(self.conn, old, self.result), 'superseded')
        self.assertEqual(save_metadata_failure(self.conn, old,
            MetadataRetrievalError('timeout', 'ignored', retryable=True)), 'superseded')
        self.assertEqual(dict(self.row()), before)

    def test_concurrent_starts_serialize_generations(self):
        def start():
            conn = db.get_db(self.path)
            try:
                return begin_metadata_attempt(conn, self.sid)
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: start(), range(2)))
        self.assertEqual(len(set(attempts)), 2)
        rows = get_metadata_attempts(self.conn, self.sid)
        self.assertEqual([r['generation'] for r in rows], [2, 1])
        self.assertEqual([r['status'] for r in rows], ['pending','superseded'])

    def test_concurrent_completion_and_retry_never_restores_old_generation(self):
        old = begin_metadata_attempt(self.conn, self.sid)
        def complete():
            conn = db.get_db(self.path)
            try:
                return save_metadata_result(conn, old, self.result)
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(complete)
            current = begin_metadata_attempt(self.conn, self.sid)
            self.assertIn(future.result(), ('available','superseded'))
        save_metadata_result(self.conn, current, replace(self.result, title='Generation two'))
        self.assertEqual(self.row()['source_title'], 'Generation two')
        self.assertEqual(self.row()['metadata_generation'], 2)

    def test_repeated_completion_rejected_without_mutation(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, attempt, self.result)
        before = dict(self.row())
        with self.assertRaises(ValueError):
            save_metadata_result(self.conn, attempt, self.result)
        with self.assertRaises(ValueError):
            save_metadata_failure(self.conn, attempt, MetadataRetrievalError('timeout','ignored'))
        self.assertEqual(dict(self.row()), before)

    def test_identity_mismatch_and_forgery_rejected(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        for source in (normalize_source_url('https://youtu.be/abcdefghijk'),
                       normalize_source_url('https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC'),
                       replace(self.source, canonical_url='https://evil.test')):
            with self.subTest(source=source), self.assertRaises(ValueError):
                save_metadata_result(self.conn, attempt, replace(self.result, source=source))
        self.assertEqual(get_metadata_attempts(self.conn, self.sid)[0]['status'], 'pending')

    def test_inconsistent_stored_identity_rejected(self):
        sid = self.conn.execute("""INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url)
            VALUES (1,'youtube','invalid','https://youtu.be/abcdefghijk','https://www.youtube.com/watch?v=abcdefghijk')""").lastrowid
        self.conn.commit()
        with self.assertRaises(ValueError):
            begin_metadata_attempt(self.conn, sid)

    def test_duration_validation(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        for value in (-1, float('nan'), float('inf'), -float('inf'), True, '12', 10**1000):
            with self.subTest(value=str(value)[:20]), self.assertRaises(ValueError):
                save_metadata_result(self.conn, attempt, replace(self.result, duration_seconds=value))
        self.assertEqual(self.row()['metadata_status'], 'pending')

    def test_date_validation_and_pair_merge(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        for value, precision in (('2020-02-30','day'), ('2020','day'), (None,'year'),
                                 ('wrong',None), ('2020','wrong'), ('2020',[])):
            with self.subTest(value=value, precision=precision), self.assertRaises(ValueError):
                save_metadata_result(self.conn, attempt, replace(self.result,
                    publication_date=value, publication_date_precision=precision))
        save_metadata_result(self.conn, attempt, self.result)
        attempt = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, attempt, replace(self.result, publication_date_precision=None))
        self.assertEqual(self.row()['publication_date_precision'], 'second')
        attempt = begin_metadata_attempt(self.conn, self.sid)
        save_metadata_result(self.conn, attempt, replace(self.result, publication_date='2021',publication_date_precision=None))
        self.assertEqual(self.row()['publication_date'], '2021')
        self.assertIsNone(self.row()['publication_date_precision'])

    def test_text_artwork_and_view_validation(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        for field, value in (('title', []), ('description','bad\x00text'), ('artwork_url','javascript:bad'),
                             ('youtube_view_count', True), ('youtube_view_count',-1), ('youtube_view_count',2**63)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                save_metadata_result(self.conn, attempt, replace(self.result, **{field:value}))

    def test_error_validation(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        for error in (ValueError('secret'), MetadataRetrievalError('untrusted text','secret'),
                      MetadataRetrievalError('timeout','secret',retryable=1)):
            with self.assertRaises(ValueError):
                save_metadata_failure(self.conn, attempt, error)
        for delay in (-1, True, '60', 10**12):
            error = MetadataRetrievalError('timeout','secret',retryable=True)
            error.retry_after_seconds = delay
            with self.assertRaises(ValueError):
                save_metadata_failure(self.conn, attempt, error)
        self.assertEqual(get_metadata_attempts(self.conn,self.sid)[0]['status'],'pending')

    def test_rejected_submission_and_late_results(self):
        attempt = begin_metadata_attempt(self.conn, self.sid)
        self.reject()
        with self.assertRaises(ValueError):
            begin_metadata_attempt(self.conn, self.sid)
        before = dict(self.row())
        self.assertEqual(save_metadata_result(self.conn, attempt, self.result), 'superseded')
        self.assertEqual(dict(self.row()), before)

    def test_spotify_disabled_failure_persisted_without_activation(self):
        source = normalize_source_url('https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC')
        sid = self.submission(source.source_url)
        self.conn.commit()
        attempt = begin_metadata_attempt(self.conn, sid)
        with patch.dict(os.environ, {'SPOTIFY_METADATA_MODE':'disabled'}):
            try:
                SpotifyMetadataClient().fetch(source)
            except MetadataRetrievalError as error:
                self.assertEqual(save_metadata_failure(self.conn, attempt, error), 'failed')
            else:
                self.fail('Spotify should remain disabled')
            self.assertEqual(os.environ['SPOTIFY_METADATA_MODE'],'disabled')

    def test_spotify_snapshot_is_not_copied_to_catalog(self):
        source = normalize_source_url('https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC')
        sid = self.submission(source.source_url)
        self.conn.commit()
        attempt = begin_metadata_attempt(self.conn, sid)
        result = TrackMetadata(source,title='Spotify',artist='A, B',duration_seconds=90,
            artwork_url='https://i.scdn.co/fake',publication_date='2020',publication_date_precision='year')
        self.assertEqual(save_metadata_result(self.conn,attempt,result),'available')
        row = self.conn.execute('SELECT * FROM submissions WHERE id=?',(sid,)).fetchone()
        self.assertIsNone(row['artwork_path'])
        self.assertIsNone(row['title'])
        self.assertIsNone(row['track_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],0)

    def test_history_limits_and_read_only_access(self):
        for _ in range(3):
            begin_metadata_attempt(self.conn,self.sid)
        rows = get_metadata_attempts(self.conn,self.sid,limit=2)
        self.assertEqual([r['generation'] for r in rows],[3,2])
        self.assertFalse(self.conn.in_transaction)
        for limit in (0,201,True,'2'):
            with self.assertRaises(ValueError):
                get_metadata_attempts(self.conn,self.sid,limit=limit)

    def test_nested_transaction_and_disabled_foreign_keys_rejected(self):
        self.conn.execute('BEGIN')
        with self.assertRaises(ValueError):
            begin_metadata_attempt(self.conn,self.sid)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.conn.execute('PRAGMA foreign_keys=OFF')
        with self.assertRaises(ValueError):
            begin_metadata_attempt(self.conn,self.sid)

    def test_sql_constraints_and_history_protection(self):
        attempt = begin_metadata_attempt(self.conn,self.sid)
        for sql in (
            "UPDATE submissions SET source_duration_seconds=-1",
            "UPDATE submissions SET source_duration_seconds=1e999",
            "UPDATE submissions SET metadata_generation=-1",
            "UPDATE submissions SET metadata_generation=0",
            "UPDATE submission_metadata_attempts SET status='invented'",
            "UPDATE submission_metadata_attempts SET status='failed',finished_at='now',error_code='secret'",
            "UPDATE submission_metadata_attempts SET status='partial'",
            "DELETE FROM submission_metadata_attempts",
            "DELETE FROM submissions",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql)
        self.conn.rollback()
        save_metadata_result(self.conn,attempt,self.result)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE submission_metadata_attempts SET error_code='timeout' WHERE id=?",(attempt,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('INSERT OR REPLACE INTO submission_metadata_attempts(id,submission_id,generation) VALUES (?,?,1)',(attempt,self.sid))

    def test_failure_to_finish_rolls_back_snapshot(self):
        attempt = begin_metadata_attempt(self.conn,self.sid)
        before = dict(self.row())
        self.conn.execute("""CREATE TRIGGER fail_finish BEFORE UPDATE ON submission_metadata_attempts
            WHEN NEW.status='available' BEGIN SELECT RAISE(ABORT,'test'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            save_metadata_result(self.conn,attempt,self.result)
        self.assertEqual(dict(self.row(self.connect())),before)
        self.assertEqual(get_metadata_attempts(self.conn,self.sid)[0]['status'],'pending')

    def test_failure_to_update_submission_rolls_back_error_history(self):
        attempt = begin_metadata_attempt(self.conn,self.sid)
        self.conn.execute("""CREATE TRIGGER fail_update BEFORE UPDATE ON submissions
            WHEN NEW.metadata_status='failed' BEGIN SELECT RAISE(ABORT,'test'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            save_metadata_failure(self.conn,attempt,MetadataRetrievalError('timeout','ignored'))
        self.assertEqual(get_metadata_attempts(self.connect(),self.sid)[0]['status'],'pending')

    def test_duplicate_rules_unchanged(self):
        begin_metadata_attempt(self.conn,self.sid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.submission()
        self.conn.rollback()
        self.submission(owner=3)
        self.conn.commit()
        self.reject()
        self.submission()
        self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submissions').fetchone()[0],3)

    def test_migrate_stage1_database_and_rerun(self):
        directory = Path(self.temp.name)/'stage1'
        directory.mkdir()
        migration = ROOT/'migrations/001_submissions.sql'
        (directory/migration.name).write_bytes(migration.read_bytes())
        path = Path(self.temp.name)/'stage1.sqlite'
        conn = db.get_db(path)
        self.addCleanup(conn.close)
        conn.executescript((ROOT/'schema.sql').read_text())
        db.migrate_db(path,migrations_dir=directory)
        conn.execute("INSERT INTO users(id,email) VALUES (1,'legacy@example.test')")
        conn.execute("""INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url,source_title)
            VALUES (1,'youtube','dQw4w9WgXcQ',?,?,'Preserved')""",(URL,self.source.canonical_url))
        conn.execute("INSERT INTO tracks(uploader_id,file_path,title,genre_id,status) VALUES (1,'tracks/legacy.mp3','Legacy',1,'approved')")
        conn.commit()
        before = tuple(conn.execute('SELECT * FROM tracks').fetchone())
        checksum = hashlib.sha256(migration.read_bytes()).hexdigest()
        db.migrate_db(path)
        db.migrate_db(path)
        self.assertEqual(tuple(conn.execute('SELECT * FROM tracks').fetchone()),before)
        row = conn.execute('SELECT * FROM submissions').fetchone()
        self.assertEqual(row['source_title'],'Preserved')
        self.assertEqual(row['metadata_generation'],0)
        self.assertIsNone(row['source_duration_seconds'])
        self.assertEqual(conn.execute('SELECT checksum FROM schema_migrations WHERE version=1').fetchone()[0],checksum)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],3)
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_migration002_failure_rolls_back_columns_and_retries(self):
        directory = Path(self.temp.name)/'broken'
        directory.mkdir()
        for name in ('001_submissions.sql','002_submission_metadata.sql'):
            (directory/name).write_bytes((ROOT/'migrations'/name).read_bytes())
        path = Path(self.temp.name)/'broken.sqlite'
        conn = db.get_db(path)
        self.addCleanup(conn.close)
        conn.executescript((ROOT/'schema.sql').read_text())
        stage1 = Path(self.temp.name)/'only001'
        stage1.mkdir()
        (stage1/'001_submissions.sql').write_bytes((directory/'001_submissions.sql').read_bytes())
        db.migrate_db(path,migrations_dir=stage1)
        migration = directory/'002_submission_metadata.sql'
        original = migration.read_text()
        migration.write_text(original+'\nINSERT INTO missing_table VALUES (1);\n')
        with self.assertRaises(sqlite3.OperationalError):
            db.migrate_db(path,migrations_dir=directory)
        self.assertNotIn('metadata_generation',[r['name'] for r in conn.execute('PRAGMA table_info(submissions)')])
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='submission_metadata_attempts'").fetchone())
        self.assertEqual([r[0] for r in conn.execute('SELECT version FROM schema_migrations')],[1])
        migration.write_text(original)
        db.migrate_db(path,migrations_dir=directory)
        self.assertIn('metadata_generation',[r['name'] for r in conn.execute('PRAGMA table_info(submissions)')])

    def test_metadata_refresh_preserves_entire_moderation_path(self):
        self.conn.execute("""UPDATE submissions SET status='pending_review',title='Editorial',
            artist='Artist',genre_id=1 WHERE id=?""",(self.sid,))
        self.conn.commit()
        for status in ('pending_review','approved_awaiting_audio','pending_audio_review','approved'):
            if status=='approved_awaiting_audio':
                self.conn.execute("""UPDATE submissions SET status=?,reviewed_by=2,
                    reviewed_at=CURRENT_TIMESTAMP WHERE id=?""",(status,self.sid))
                self.conn.commit()
            elif status=='pending_audio_review':
                tid = self.conn.execute("""INSERT INTO tracks(uploader_id,file_path,title,genre_id)
                    VALUES (1,'tracks/test.mp3','Audio',1)""").lastrowid
                add_media_proof(self.conn,self.sid,tid)
                self.conn.execute('UPDATE submissions SET status=?,track_id=? WHERE id=?',(status,tid,self.sid))
                self.conn.commit()
            elif status=='approved':
                db.finalize_audio_review(self.conn,self.sid,2,approve=True)
            before = dict(self.row())
            tracks = [tuple(r) for r in self.conn.execute('SELECT * FROM tracks')]
            attempt = begin_metadata_attempt(self.conn,self.sid)
            save_metadata_result(self.conn,attempt,self.result)
            for key in ('status','track_id','reviewed_by','reviewed_at','audio_reviewed_by',
                        'audio_reviewed_at','title','artist','genre_id'):
                self.assertEqual(self.row()[key],before[key],key)
            self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM tracks')],tracks)

    def test_missing_records_and_invalid_identifiers(self):
        for value in (0,-1,True,'1',999):
            with self.subTest(value=value), self.assertRaises(ValueError):
                begin_metadata_attempt(self.conn,value)
        with self.assertRaises(ValueError):
            save_metadata_result(self.conn,999,self.result)
        with self.assertRaises(ValueError):
            save_metadata_failure(self.conn,999,MetadataRetrievalError('timeout','ignored'))


if __name__ == '__main__':
    unittest.main()
