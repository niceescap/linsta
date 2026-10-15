"""SQL-only synthetic evidence for legacy regression fixtures (no media files).

The draft is NOT legally validated. Individual test connections explicitly mock
only the final-readiness function so pre-existing moderation tests can exercise
Stage 1 transactions. Production connections remain closed to final approval.
"""
import hashlib

from rights import DRAFT_DOCUMENT, document_matches


def add_media_proof(conn, sid, tid, *, simulate_legal_validation=True):
    track = conn.execute('SELECT * FROM tracks WHERE id=?',(tid,)).fetchone()
    path = track['file_path']
    if not path.startswith('tracks/'):
        path = 'tracks/' + path
    conn.execute('''UPDATE tracks SET file_path=?,file_size=23,duration_seconds=120,
        meta_path=? WHERE id=?''',(path,f'{tid}.jpg',tid))
    bundle = conn.execute('''INSERT INTO submission_media_bundles
        (submission_id,contributor_id,track_id,audio_path,audio_sha256,audio_size,audio_duration_seconds,
         cover_original_path,cover_original_sha256,cover_original_size,cover_original_mime,
         cover_original_width,cover_original_height) VALUES (?,?,?,?,?,23,120,?,?,1,'image/jpeg',1,1)''',
        (sid,track['uploader_id'],tid,path,hashlib.sha256(b'synthetic-audio').hexdigest(),
         f'{tid}.jpg',hashlib.sha256(b'synthetic-cover').hexdigest())).lastrowid
    conn.execute('''INSERT INTO submission_authorizations
        (bundle_id,contributor_id,declared_name,declared_artist,declarant_capacity,
         music_accepted,visual_accepted,document_version,document_text,document_sha256)
        VALUES (?,?,'Test declarant','Test artist','rights_holder',1,1,?,?,?)''',
        (bundle,track['uploader_id'],DRAFT_DOCUMENT.version,DRAFT_DOCUMENT.text,DRAFT_DOCUMENT.sha256))
    if simulate_legal_validation:
        conn.create_function('digger_document_ready',3,document_matches)
    return bundle
