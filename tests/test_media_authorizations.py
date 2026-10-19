"""Migration 003 integrity and transaction tests; no real media or application DB.

Successful final-approval tests explicitly simulate a legally validated registry
on their isolated connection. No such version exists in the production module.
"""
import hashlib
import concurrent.futures
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest

import db
from rights import DRAFT_DOCUMENT, document_matches

ROOT = Path(__file__).resolve().parents[1]


class MediaAuthorizationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.path = self.directory/'media.sqlite'
        db.init_db(self.path)
        self.conn = db.get_db(self.path)
        self.addCleanup(self.conn.close)
        self.conn.executemany('INSERT INTO users(id,email,role,is_verified) VALUES (?,?,?,1)',
            [(1,'contributor@example.test','contributor'),(2,'admin@example.test','admin'),
             (3,'other@example.test','contributor')])
        self.sid,self.tid = self.prepare()
        self.conn.commit()

    def prepare(self,owner=1,source='video'):
        sid = self.conn.execute('''INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url)
            VALUES (?,'youtube',?,'https://youtu.be/dQw4w9WgXcQ','https://www.youtube.com/watch?v=dQw4w9WgXcQ')''',
            (owner,source)).lastrowid
        self.conn.execute("UPDATE submissions SET status='pending_review',title='Title',artist='Artist',genre_id=1 WHERE id=?",(sid,))
        self.conn.execute("UPDATE submissions SET status='approved_awaiting_audio',reviewed_by=2,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",(sid,))
        tid = self.conn.execute('''INSERT INTO tracks(uploader_id,file_path,file_size,duration_seconds,meta_path,title,genre_id)
            VALUES (?,?,1024,30,?,'Title',1)''',(owner,f'tracks/{source}.mp3',f'{source}.jpg')).lastrowid
        return sid,tid

    def bundle_values(self,**changes):
        values = dict(submission_id=self.sid,contributor_id=1,track_id=self.tid,
            audio_path='tracks/video.mp3',audio_sha256=hashlib.sha256(b'audio').hexdigest(),
            audio_size=1024,audio_duration_seconds=30,cover_original_path='video.jpg',
            cover_original_sha256=hashlib.sha256(b'cover').hexdigest(),cover_original_size=512,
            cover_original_mime='image/jpeg',cover_original_width=100,cover_original_height=100)
        values.update(changes)
        return values

    def insert(self,table,values,verb='INSERT'):
        columns=','.join(values)
        return self.conn.execute(f'{verb} INTO {table} ({columns}) VALUES ({",".join("?" for _ in values)})',
                                 tuple(values.values())).lastrowid

    def bundle(self,**changes):
        return self.insert('submission_media_bundles',self.bundle_values(**changes))

    def authorization_values(self,bid,**changes):
        d=DRAFT_DOCUMENT
        values=dict(bundle_id=bid,contributor_id=1,declared_name='Declarant',declared_artist='Artist',
            declarant_capacity='rights_holder',music_accepted=1,visual_accepted=1,
            document_version=d.version,document_text=d.text,document_sha256=d.sha256)
        values.update(changes)
        return values

    def authorize(self,bid,**changes):
        return self.insert('submission_authorizations',self.authorization_values(bid,**changes))

    def link(self):
        self.conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(self.tid,self.sid))

    def complete(self):
        bid=self.bundle()
        self.authorize(bid)
        self.link()
        self.conn.commit()
        return bid

    def simulate_validated_document(self):
        # Test double only. The real registry deliberately approves no version.
        self.conn.create_function('digger_document_ready',3,document_matches)

    def test_fresh_init_and_rerun(self):
        db.init_db(self.path)
        db.migrate_db(self.path)
        self.assertEqual([r[0] for r in self.conn.execute('SELECT version FROM schema_migrations ORDER BY version')],[1,2,3])
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_upgrade_stage1_and_stage2_preserves_every_existing_row(self):
        for versions in ((1,),(1,2)):
            with self.subTest(versions=versions):
                directory=self.directory/('old'+str(len(versions)))
                directory.mkdir()
                for filename in ('001_submissions.sql','002_submission_metadata.sql')[:len(versions)]:
                    shutil.copyfile(ROOT/'migrations'/filename,directory/filename)
                path=self.directory/f'old{len(versions)}.sqlite'
                conn=db.get_db(path)
                self.addCleanup(conn.close)
                conn.executescript((ROOT/'schema.sql').read_text())
                db.migrate_db(path,migrations_dir=directory)
                conn.execute("INSERT INTO users(id,email) VALUES (1,'legacy@example.test')")
                for status in ('pending','approved','rejected'):
                    conn.execute("INSERT INTO tracks(uploader_id,file_path,title,genre_id,status) VALUES (1,?,'Legacy',1,?)",(f'tracks/{status}.mp3',status))
                conn.execute("INSERT INTO plays(track_id,started_at) VALUES (2,'2026-01-01')")
                conn.execute("INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url) VALUES (1,'youtube','old','source','canonical')")
                conn.commit()
                tables=('users','genres','tracks','plays','submissions')
                before={t:[dict(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
                columns={t:[r['name'] for r in conn.execute(f'PRAGMA table_info({t})')] for t in tables}
                db.migrate_db(path)
                db.migrate_db(path)
                for table in tables:
                    self.assertEqual(before[table],[{key:r[key] for key in columns[table]} for r in conn.execute(f'SELECT * FROM {table}')])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM submission_media_bundles').fetchone()[0],0)
                self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_complete_write_order_is_possible_in_single_transaction(self):
        observer=db.get_db(self.path)
        self.addCleanup(observer.close)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            bid=self.bundle()
            self.authorize(bid)
            self.link()
            self.assertEqual(observer.execute('SELECT COUNT(*) FROM submission_media_bundles').fetchone()[0],0)
            self.assertIsNone(observer.execute('SELECT track_id FROM submissions WHERE id=?',(self.sid,)).fetchone()[0])
        self.assertEqual(observer.execute('SELECT status FROM submissions WHERE id=?',(self.sid,)).fetchone()[0],'pending_audio_review')
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM submission_authorizations').fetchone()[0],1)
        self.assertEqual(observer.execute('SELECT status FROM tracks WHERE id=?',(self.tid,)).fetchone()[0],'pending')

    def test_link_requires_bundle_and_complete_authorization(self):
        with self.assertRaises(sqlite3.IntegrityError): self.link()
        self.bundle()
        with self.assertRaises(sqlite3.IntegrityError): self.link()
        self.assertIsNone(self.conn.execute('SELECT track_id FROM submissions WHERE id=?',(self.sid,)).fetchone()[0])

    def test_both_acceptances_required_without_null_bypass(self):
        bid=self.bundle()
        for field in ('music_accepted','visual_accepted'):
            for value in (None,0,-1,2,'yes'):
                with self.subTest(field=field,value=value), self.assertRaises(sqlite3.IntegrityError):
                    self.authorize(bid,**{field:value})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submission_authorizations').fetchone()[0],0)

    def test_audio_cover_fields_mandatory(self):
        for field in self.bundle_values():
            with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError):
                self.bundle(**{field:None})

    def test_source_must_be_accepted_and_track_owned_pending_matching(self):
        for field,value in (('contributor_id',3),('track_id',999),('submission_id',999),
                            ('audio_path','tracks/wrong.mp3'),('audio_size',1),
                            ('audio_duration_seconds',31),('cover_original_path','wrong.jpg')):
            with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError):
                self.bundle(**{field:value})
        self.conn.execute("UPDATE tracks SET status='approved' WHERE id=?",(self.tid,))
        with self.assertRaises(sqlite3.IntegrityError): self.bundle()
        self.conn.rollback()
        draft=self.conn.execute("INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url) VALUES (1,'youtube','draft','source','canonical')").lastrowid
        with self.assertRaises(sqlite3.IntegrityError): self.bundle(submission_id=draft)

    def test_authorization_contributor_and_bundle_must_match(self):
        bid=self.bundle()
        for changes in ({'contributor_id':3},{'bundle_id':999}):
            with self.assertRaises(sqlite3.IntegrityError): self.authorize(bid,**changes)

    def test_sha256_rejects_malformed_blob_and_hidden_suffix(self):
        for field in ('audio_sha256','cover_original_sha256'):
            for value in ('a'*63,'G'*64,'a'*64+'\0hidden',b'a'*64,''):
                with self.subTest(field=field,value=value), self.assertRaises(sqlite3.IntegrityError):
                    self.bundle(**{field:value})

    def test_document_full_text_version_and_digest_are_verified(self):
        bid=self.bundle()
        for changes in ({'document_version':'unknown'},{'document_text':DRAFT_DOCUMENT.text+' '},
                        {'document_sha256':'0'*64},{'document_sha256':None}):
            with self.assertRaises(sqlite3.IntegrityError): self.authorize(bid,**changes)
        self.authorize(bid)
        row=self.conn.execute('SELECT * FROM submission_authorizations').fetchone()
        self.assertEqual(row['document_text'],DRAFT_DOCUMENT.text)
        self.assertEqual(row['document_sha256'],hashlib.sha256(row['document_text'].encode()).hexdigest())

    def test_dates_and_identity_are_strict(self):
        bid=self.bundle()
        for date in ('now','2026-02-30T00:00:00Z','2026-01-01','2026-01-01T00:00:00+01:00'):
            with self.assertRaises(sqlite3.IntegrityError): self.authorize(bid,accepted_at=date)
        for changes in ({'declared_name':' '},{'declared_artist':'\t'},
                        {'declared_name':'bad\nname'},{'declarant_capacity':'unknown'}):
            with self.assertRaises(sqlite3.IntegrityError): self.authorize(bid,**changes)
        self.authorize(bid)
        self.assertRegex(self.conn.execute('SELECT accepted_at FROM submission_authorizations').fetchone()[0],r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')

    def test_normalized_cover_all_or_nothing_and_display_binding(self):
        with self.assertRaises(sqlite3.IntegrityError): self.bundle(cover_normalized_path='normalized.png')
        values=dict(cover_normalized_path='normalized.png',cover_normalized_sha256='b'*64,
            cover_normalized_size=500,cover_normalized_mime='image/png',cover_normalized_width=80,cover_normalized_height=80)
        for field in values:
            changes=values.copy()
            changes[field]=None
            with self.subTest(field=field), self.assertRaises(sqlite3.IntegrityError): self.bundle(**changes)
        with self.assertRaises(sqlite3.IntegrityError): self.bundle(**values)  # track still points to original
        self.conn.execute("UPDATE tracks SET meta_path='normalized.png' WHERE id=?",(self.tid,))
        bid=self.bundle(**values)
        self.authorize(bid)
        self.link()
        self.assertEqual(self.conn.execute('SELECT cover_original_sha256 FROM submission_media_bundles').fetchone()[0],hashlib.sha256(b'cover').hexdigest())

    def test_unsafe_paths_sizes_and_durations_rejected(self):
        for changes in ({'audio_path':'../audio.mp3'},{'cover_original_path':'../cover.jpg'},
                        {'cover_original_path':'cover.jpg\0evil'},{'cover_original_path':'cover.svg'},
                        {'audio_size':0},{'cover_original_size':0},{'cover_original_width':0},
                        {'audio_duration_seconds':float('inf')},{'audio_duration_seconds':float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(sqlite3.IntegrityError): self.bundle(**changes)

    def test_evidence_is_immutable_and_cannot_be_deleted(self):
        self.complete()
        for table in ('submission_media_bundles','submission_authorizations'):
            for sql in (f'UPDATE {table} SET id=id',f'DELETE FROM {table}'):
                with self.assertRaises(sqlite3.IntegrityError): self.conn.execute(sql)

    def test_replace_and_upsert_cannot_substitute_proof(self):
        bid=self.complete()
        for table in ('submission_media_bundles','submission_authorizations'):
            row=dict(self.conn.execute(f'SELECT * FROM {table}').fetchone())
            for verb in ('INSERT OR REPLACE','REPLACE'):
                with self.assertRaises(sqlite3.IntegrityError): self.insert(table,row,verb)
            columns=','.join(row)
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)}) ON CONFLICT(id) DO UPDATE SET id=id',tuple(row.values()))
        self.assertEqual(self.conn.execute('SELECT bundle_id FROM submission_authorizations').fetchone()[0],bid)

    def test_track_identity_and_cover_frozen_before_and_after_link(self):
        bid=self.bundle()
        for linked in (False,True):
            if linked: self.authorize(bid); self.link()
            for assignment in ("file_path='tracks/other.mp3'","meta_path='other.jpg'",'file_size=99',
                               'duration_seconds=1','uploader_id=3','id=999',"mime_type='text/plain'",
                               "original_filename='other.mp3'"):
                with self.subTest(linked=linked,assignment=assignment), self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(f'UPDATE tracks SET {assignment} WHERE id=?',(self.tid,))
            with self.assertRaises(sqlite3.IntegrityError): self.conn.execute('DELETE FROM tracks WHERE id=?',(self.tid,))
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute('''INSERT OR REPLACE INTO tracks(id,uploader_id,file_path,title,genre_id)
                    VALUES (?,1,'tracks/replaced.mp3','Other',1)''',(self.tid,))

    def test_bundle_cannot_enable_track_before_submission_approval(self):
        self.bundle()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE tracks SET status='approved' WHERE id=?",(self.tid,))

    def test_draft_cannot_receive_final_production_approval(self):
        self.complete()
        with self.assertRaises(sqlite3.IntegrityError): db.finalize_audio_review(self.conn,self.sid,2,approve=True)
        self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(self.tid,)).fetchone()[0],'pending')
        self.assertEqual(self.conn.execute('SELECT status FROM submissions WHERE id=?',(self.sid,)).fetchone()[0],'pending_audio_review')

    def test_final_approval_order_and_commit_with_explicit_test_registry(self):
        self.complete()
        self.simulate_validated_document()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE tracks SET status='approved' WHERE id=?",(self.tid,))
        self.conn.rollback()
        db.finalize_audio_review(self.conn,self.sid,2,approve=True)
        observer=db.get_db(self.path)
        self.addCleanup(observer.close)
        self.assertEqual(observer.execute('SELECT status FROM tracks WHERE id=?',(self.tid,)).fetchone()[0],'approved')
        self.assertEqual(observer.execute('SELECT status FROM submissions WHERE id=?',(self.sid,)).fetchone()[0],'approved')
        with self.assertRaises(sqlite3.IntegrityError): self.conn.execute("UPDATE tracks SET meta_path='other.jpg' WHERE id=?",(self.tid,))

    def test_rejection_retains_document_and_both_media(self):
        self.complete()
        before=[tuple(r) for r in self.conn.execute('SELECT * FROM submission_authorizations')]
        db.finalize_audio_review(self.conn,self.sid,2,approve=False,reason='Wrong cover')
        self.assertEqual(self.conn.execute('SELECT status FROM tracks WHERE id=?',(self.tid,)).fetchone()[0],'rejected')
        self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM submission_authorizations')],before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submission_media_bundles').fetchone()[0],1)

    def test_failure_after_track_insert_rolls_back_track_bundle_and_proof(self):
        count=self.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]
        observer=db.get_db(self.path)
        self.addCleanup(observer.close)
        with self.assertRaises(sqlite3.IntegrityError),self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            sid,tid=self.prepare(source='rollback')
            bid=self.bundle(submission_id=sid,track_id=tid,audio_path='tracks/rollback.mp3',cover_original_path='rollback.jpg')
            self.authorize(bid,visual_accepted=0)
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM tracks').fetchone()[0],count)
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM submission_media_bundles').fetchone()[0],0)
        self.assertEqual(observer.execute('SELECT COUNT(*) FROM submission_authorizations').fetchone()[0],0)

    def test_duplicate_and_cross_submission_evidence_rejected(self):
        bid=self.bundle()
        self.authorize(bid)
        with self.assertRaises(sqlite3.IntegrityError): self.bundle()
        with self.assertRaises(sqlite3.IntegrityError): self.authorize(bid)
        sid,tid=self.prepare(owner=3,source='other')
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(self.tid,sid))

    def test_plain_sqlite_connection_fails_closed_on_document_validation(self):
        self.bundle()
        self.conn.commit()
        conn=sqlite3.connect(self.path)
        self.addCleanup(conn.close)
        values=self.authorization_values(1)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute(f'INSERT INTO submission_authorizations ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',tuple(values.values()))

    def test_migration003_failure_rolls_back_and_retries_cleanly(self):
        directory=self.directory/'failed'
        directory.mkdir()
        for file in (ROOT/'migrations').glob('*.sql'): shutil.copyfile(file,directory/file.name)
        path=self.directory/'failure.sqlite'
        conn=db.get_db(path)
        self.addCleanup(conn.close)
        conn.executescript((ROOT/'schema.sql').read_text())
        migration=directory/'003_submission_media_authorizations.sql'
        original=migration.read_text()
        migration.write_text(original+'\nINSERT INTO missing_table VALUES (1);\n')
        with self.assertRaises(sqlite3.OperationalError): db.migrate_db(path,migrations_dir=directory)
        self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'submission_%'").fetchall(),[])
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone())
        migration.write_text(original)
        db.migrate_db(path,migrations_dir=directory)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],3)

    def test_preexisting_linked_pending_audio_without_proof_cannot_be_approved(self):
        # A historical linked pending row is preserved on upgrade, not given fake rights.
        path=self.directory/'pending.sqlite'
        directory=self.directory/'stage2'
        directory.mkdir()
        for name in ('001_submissions.sql','002_submission_metadata.sql'):
            shutil.copyfile(ROOT/'migrations'/name,directory/name)
        conn=db.get_db(path)
        self.addCleanup(conn.close)
        conn.executescript((ROOT/'schema.sql').read_text())
        db.migrate_db(path,migrations_dir=directory)
        conn.executescript("""INSERT INTO users(id,email,role) VALUES (1,'old@example.test','contributor'),(2,'admin@example.test','admin');
            INSERT INTO submissions(contributor_id,platform,source_id,source_url,canonical_url) VALUES (1,'youtube','old','url','url');
            UPDATE submissions SET status='pending_review',title='Old',artist='Artist',genre_id=1;
            UPDATE submissions SET status='approved_awaiting_audio',reviewed_by=2,reviewed_at=CURRENT_TIMESTAMP;
            INSERT INTO tracks(uploader_id,file_path,title,genre_id) VALUES (1,'tracks/old.mp3','Old',1);
            UPDATE submissions SET status='pending_audio_review',track_id=1;""")
        before=tuple(conn.execute('SELECT * FROM tracks').fetchone())
        db.migrate_db(path)
        self.assertEqual(tuple(conn.execute('SELECT * FROM tracks').fetchone()),before)
        with self.assertRaises(sqlite3.IntegrityError): db.finalize_audio_review(conn,1,2,approve=True)

    def test_concurrent_bundle_authorization_and_attachment_has_one_winner(self):
        barrier=threading.Barrier(2)
        values=self.bundle_values()
        def attempt():
            conn=db.get_db(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    with conn:
                        conn.execute('BEGIN IMMEDIATE')
                        bid=conn.execute(f'INSERT INTO submission_media_bundles ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',tuple(values.values())).lastrowid
                        auth=self.authorization_values(bid)
                        conn.execute(f'INSERT INTO submission_authorizations ({",".join(auth)}) VALUES ({",".join("?" for _ in auth)})',tuple(auth.values()))
                        conn.execute("UPDATE submissions SET status='pending_audio_review',track_id=? WHERE id=?",(self.tid,self.sid))
                    return 'ok'
                except sqlite3.IntegrityError:
                    self.assertFalse(conn.in_transaction)
                    return 'conflict'
            finally:
                conn.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(attempt) for _ in range(2)]
            self.assertCountEqual([f.result(timeout=10) for f in futures],['ok','conflict'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submission_media_bundles').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM submission_authorizations').fetchone()[0],1)

    def test_final_approval_failure_rolls_back_media_decision_with_proofs_intact(self):
        self.complete()
        self.simulate_validated_document()
        self.conn.execute("""CREATE TRIGGER fail_final BEFORE UPDATE OF status ON tracks
            WHEN NEW.status='approved' BEGIN SELECT RAISE(ABORT,'test failure'); END""")
        before=[tuple(r) for r in self.conn.execute('SELECT * FROM submission_authorizations')]
        with self.assertRaises(sqlite3.IntegrityError): db.finalize_audio_review(self.conn,self.sid,2,approve=True)
        observer=db.get_db(self.path)
        self.addCleanup(observer.close)
        self.assertEqual(observer.execute('SELECT status FROM submissions WHERE id=?',(self.sid,)).fetchone()[0],'pending_audio_review')
        self.assertEqual(observer.execute('SELECT status FROM tracks WHERE id=?',(self.tid,)).fetchone()[0],'pending')
        self.assertEqual([tuple(r) for r in observer.execute('SELECT * FROM submission_authorizations')],before)
