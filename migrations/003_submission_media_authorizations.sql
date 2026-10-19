-- Additive media evidence only. No existing row, file or moderation decision is rewritten.
-- Safe write order, one transaction: pending track -> bundle -> authorization -> link.
-- A prepared bundle is not an authorization and cannot make its track broadcastable.
-- db.get_db() supplies the document registry functions; unknown/unregistered connections
-- fail closed on authorization inserts and final approval. No production document exists yet.
CREATE TABLE submission_media_bundles (
    id INTEGER PRIMARY KEY,
    submission_id INTEGER NOT NULL UNIQUE REFERENCES submissions(id),
    contributor_id INTEGER NOT NULL REFERENCES users(id),
    track_id INTEGER NOT NULL UNIQUE REFERENCES tracks(id),
    audio_path TEXT NOT NULL UNIQUE CHECK (
        substr(audio_path,1,7)='tracks/' AND length(audio_path)>11
        AND substr(audio_path,-4)='.mp3'
        AND substr(audio_path,8) NOT GLOB '*[^a-zA-Z0-9_.-]*' AND instr(audio_path,char(0))=0),
    audio_sha256 TEXT NOT NULL CHECK (typeof(audio_sha256)='text' AND length(CAST(audio_sha256 AS BLOB))=64 AND audio_sha256 NOT GLOB '*[^0-9a-f]*'),
    audio_size INTEGER NOT NULL CHECK (typeof(audio_size)='integer' AND audio_size>0),
    audio_duration_seconds REAL NOT NULL CHECK (typeof(audio_duration_seconds) IN ('integer','real')
        AND audio_duration_seconds>0 AND audio_duration_seconds<=1.7976931348623157e308),
    cover_original_path TEXT NOT NULL UNIQUE CHECK (length(cover_original_path)>4
        AND cover_original_path NOT GLOB '*[^a-zA-Z0-9_.-]*' AND instr(cover_original_path,char(0))=0),
    cover_original_sha256 TEXT NOT NULL CHECK (typeof(cover_original_sha256)='text' AND length(CAST(cover_original_sha256 AS BLOB))=64
        AND cover_original_sha256 NOT GLOB '*[^0-9a-f]*'),
    cover_original_size INTEGER NOT NULL CHECK (typeof(cover_original_size)='integer' AND cover_original_size>0),
    cover_original_mime TEXT NOT NULL CHECK (cover_original_mime IN ('image/jpeg','image/png')),
    cover_original_width INTEGER NOT NULL CHECK (typeof(cover_original_width)='integer' AND cover_original_width>0),
    cover_original_height INTEGER NOT NULL CHECK (typeof(cover_original_height)='integer' AND cover_original_height>0),
    cover_normalized_path TEXT UNIQUE,
    cover_normalized_sha256 TEXT,
    cover_normalized_size INTEGER,
    cover_normalized_mime TEXT,
    cover_normalized_width INTEGER,
    cover_normalized_height INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CHECK ((cover_original_mime='image/jpeg' AND (substr(cover_original_path,-4)='.jpg'
            OR substr(cover_original_path,-5)='.jpeg'))
        OR (cover_original_mime='image/png' AND substr(cover_original_path,-4)='.png')),
    CHECK ((cover_normalized_path IS NULL AND cover_normalized_sha256 IS NULL
        AND cover_normalized_size IS NULL AND cover_normalized_mime IS NULL
        AND cover_normalized_width IS NULL AND cover_normalized_height IS NULL)
        OR (cover_normalized_path IS NOT NULL AND cover_normalized_sha256 IS NOT NULL
        AND cover_normalized_size IS NOT NULL AND cover_normalized_mime IS NOT NULL
        AND cover_normalized_width IS NOT NULL AND cover_normalized_height IS NOT NULL
        AND length(cover_normalized_path)>4 AND cover_normalized_path!=cover_original_path
        AND cover_normalized_path NOT GLOB '*[^a-zA-Z0-9_.-]*' AND instr(cover_normalized_path,char(0))=0
        AND typeof(cover_normalized_sha256)='text' AND length(CAST(cover_normalized_sha256 AS BLOB))=64 AND cover_normalized_sha256 NOT GLOB '*[^0-9a-f]*'
        AND typeof(cover_normalized_size)='integer' AND cover_normalized_size>0
        AND typeof(cover_normalized_width)='integer' AND cover_normalized_width>0
        AND typeof(cover_normalized_height)='integer' AND cover_normalized_height>0
        AND ((cover_normalized_mime='image/jpeg' AND (substr(cover_normalized_path,-4)='.jpg'
                OR substr(cover_normalized_path,-5)='.jpeg'))
            OR (cover_normalized_mime='image/png' AND substr(cover_normalized_path,-4)='.png')))),
    CHECK (CASE WHEN length(created_at)=20 AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
            THEN strftime('%Y-%m-%dT%H:%M:%SZ',julianday(created_at)) IS created_at ELSE 0 END)
);

CREATE TABLE submission_authorizations (
    id INTEGER PRIMARY KEY,
    bundle_id INTEGER NOT NULL UNIQUE REFERENCES submission_media_bundles(id),
    contributor_id INTEGER NOT NULL REFERENCES users(id),
    declared_name TEXT NOT NULL CHECK (digger_identity_valid(declared_name)=1),
    declared_artist TEXT NOT NULL CHECK (digger_identity_valid(declared_artist)=1),
    declarant_capacity TEXT NOT NULL CHECK (declarant_capacity IN ('rights_holder','authorized_representative')),
    music_accepted INTEGER NOT NULL CHECK (typeof(music_accepted)='integer' AND music_accepted=1),
    visual_accepted INTEGER NOT NULL CHECK (typeof(visual_accepted)='integer' AND visual_accepted=1),
    accepted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        CHECK (CASE WHEN length(accepted_at)=20 AND accepted_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
            THEN strftime('%Y-%m-%dT%H:%M:%SZ',julianday(accepted_at)) IS accepted_at ELSE 0 END),
    document_version TEXT NOT NULL CHECK (length(trim(document_version))>0),
    document_text TEXT NOT NULL CHECK (length(trim(document_text))>0),
    document_sha256 TEXT NOT NULL CHECK (typeof(document_sha256)='text' AND length(CAST(document_sha256 AS BLOB))=64
        AND document_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (digger_document_matches(document_version,document_text,document_sha256)=1)
);

CREATE TRIGGER media_bundle_insert BEFORE INSERT ON submission_media_bundles
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM submission_media_bundles WHERE id=NEW.id
        OR submission_id=NEW.submission_id OR track_id=NEW.track_id OR audio_path=NEW.audio_path
        OR cover_original_path IN (NEW.cover_original_path,NEW.cover_normalized_path)
        OR cover_normalized_path IN (NEW.cover_original_path,NEW.cover_normalized_path))
        THEN RAISE(ABORT,'media evidence cannot be replaced') END;
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM submissions s JOIN tracks t ON t.id=NEW.track_id
        WHERE s.id=NEW.submission_id AND s.status='approved_awaiting_audio' AND s.track_id IS NULL
        AND s.contributor_id=NEW.contributor_id AND t.uploader_id=NEW.contributor_id
        AND t.status='pending' AND t.file_path=NEW.audio_path AND t.file_size=NEW.audio_size
        AND t.duration_seconds=NEW.audio_duration_seconds AND t.mime_type='audio/mpeg'
        AND t.meta_path=COALESCE(NEW.cover_normalized_path,NEW.cover_original_path))
        THEN RAISE(ABORT,'media must match an accepted source and its pending owned track') END;
END;
CREATE TRIGGER media_bundle_no_update BEFORE UPDATE ON submission_media_bundles
BEGIN
    SELECT RAISE(ABORT,'media evidence is immutable');
END;
CREATE TRIGGER media_bundle_no_delete BEFORE DELETE ON submission_media_bundles
BEGIN
    SELECT RAISE(ABORT,'media evidence cannot be deleted');
END;

CREATE TRIGGER authorization_insert BEFORE INSERT ON submission_authorizations
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM submission_authorizations WHERE id=NEW.id OR bundle_id=NEW.bundle_id)
        THEN RAISE(ABORT,'authorization cannot be replaced') END;
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM submission_media_bundles b JOIN submissions s ON s.id=b.submission_id
        WHERE b.id=NEW.bundle_id AND b.contributor_id=NEW.contributor_id
        AND s.contributor_id=NEW.contributor_id AND s.status='approved_awaiting_audio' AND s.track_id IS NULL)
        THEN RAISE(ABORT,'authorization must belong to the pending media bundle contributor') END;
END;
CREATE TRIGGER authorization_no_update BEFORE UPDATE ON submission_authorizations
BEGIN
    SELECT RAISE(ABORT,'authorization is immutable');
END;
CREATE TRIGGER authorization_no_delete BEFORE DELETE ON submission_authorizations
BEGIN
    SELECT RAISE(ABORT,'authorization cannot be deleted');
END;

CREATE TRIGGER media_link_requires_authorization BEFORE UPDATE OF track_id ON submissions
WHEN NEW.track_id IS NOT NULL AND OLD.track_id IS NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM submission_media_bundles b
        JOIN submission_authorizations a ON a.bundle_id=b.id
        WHERE b.submission_id=NEW.id AND b.track_id=NEW.track_id
        AND b.contributor_id=NEW.contributor_id AND a.contributor_id=NEW.contributor_id)
        THEN RAISE(ABORT,'complete music and visual authorization required before attachment') END;
END;
CREATE TRIGGER media_final_approval BEFORE UPDATE OF status ON submissions
WHEN NEW.status='approved' AND OLD.status!='approved'
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM submission_media_bundles b
        JOIN submission_authorizations a ON a.bundle_id=b.id
        WHERE b.submission_id=NEW.id AND b.track_id=NEW.track_id AND b.contributor_id=NEW.contributor_id
        AND a.contributor_id=NEW.contributor_id
        AND digger_document_ready(a.document_version,a.document_text,a.document_sha256)=1)
        THEN RAISE(ABORT,'complete production-validated music and visual authorization required') END;
END;

-- Protect the track as soon as the bundle exists, even before attachment.
CREATE TRIGGER media_track_immutable BEFORE UPDATE ON tracks
WHEN EXISTS (SELECT 1 FROM submission_media_bundles WHERE track_id=OLD.id) AND (
    NEW.id IS NOT OLD.id OR NEW.uploader_id IS NOT OLD.uploader_id
    OR NEW.file_path IS NOT OLD.file_path OR NEW.file_size IS NOT OLD.file_size
    OR NEW.duration_seconds IS NOT OLD.duration_seconds OR NEW.mime_type IS NOT OLD.mime_type
    OR NEW.original_filename IS NOT OLD.original_filename OR NEW.meta_path IS NOT OLD.meta_path)
BEGIN
    SELECT RAISE(ABORT,'authorized audio and cover identity is immutable');
END;
CREATE TRIGGER media_track_no_replace BEFORE INSERT ON tracks
WHEN EXISTS (SELECT 1 FROM submission_media_bundles WHERE track_id=NEW.id)
BEGIN
    SELECT RAISE(ABORT,'authorized track cannot be replaced');
END;
CREATE TRIGGER media_track_no_delete BEFORE DELETE ON tracks
WHEN EXISTS (SELECT 1 FROM submission_media_bundles WHERE track_id=OLD.id)
BEGIN
    SELECT RAISE(ABORT,'authorized track cannot be deleted');
END;
CREATE TRIGGER media_track_approval BEFORE UPDATE OF status ON tracks
WHEN NEW.status='approved' AND EXISTS (SELECT 1 FROM submission_media_bundles WHERE track_id=OLD.id)
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM submission_media_bundles b
        JOIN submission_authorizations a ON a.bundle_id=b.id JOIN submissions s ON s.id=b.submission_id
        WHERE b.track_id=OLD.id AND s.track_id=OLD.id AND s.status='approved'
        AND digger_document_ready(a.document_version,a.document_text,a.document_sha256)=1)
        THEN RAISE(ABORT,'final submission approval and validated authorization required') END;
END;
