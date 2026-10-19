-- Additive: no legacy track, user, genre or play rows are rewritten.
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contributor_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL CHECK (platform IN ('youtube', 'spotify')),
    source_id TEXT NOT NULL CHECK (length(trim(source_id)) > 0),
    source_url TEXT NOT NULL CHECK (length(trim(source_url)) > 0),
    canonical_url TEXT NOT NULL CHECK (length(trim(canonical_url)) > 0),
    source_title TEXT,
    artist_name TEXT,
    channel_name TEXT,
    artwork_url TEXT,
    artwork_path TEXT,
    publication_date TEXT,
    publication_date_precision TEXT CHECK (publication_date_precision IN ('year', 'month', 'day', 'second')),
    youtube_view_count INTEGER CHECK (youtube_view_count IS NULL
        OR (typeof(youtube_view_count) = 'integer' AND youtube_view_count >= 0 AND platform = 'youtube')),
    metadata_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (metadata_status IN ('pending', 'available', 'partial', 'failed')),
    metadata_error_code TEXT,
    metadata_fetched_at TEXT,
    title TEXT,
    artist TEXT,
    genre_id INTEGER REFERENCES genres(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
        ('draft', 'pending_review', 'approved_awaiting_audio', 'pending_audio_review', 'approved', 'rejected')),
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TEXT,
    audio_reviewed_by INTEGER REFERENCES users(id),
    audio_reviewed_at TEXT,
    rejection_reason TEXT,
    track_id INTEGER UNIQUE REFERENCES tracks(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status = 'draft' OR (title IS NOT NULL AND length(trim(title)) > 0
        AND artist IS NOT NULL AND length(trim(artist)) > 0 AND genre_id IS NOT NULL)),
    CHECK (status NOT IN ('approved_awaiting_audio', 'pending_audio_review', 'approved')
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
    CHECK (status != 'approved' OR (audio_reviewed_by IS NOT NULL AND audio_reviewed_at IS NOT NULL)),
    CHECK (status != 'rejected' OR (rejection_reason IS NOT NULL
        AND length(trim(rejection_reason)) > 0 AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
    CHECK ((status IN ('draft', 'pending_review', 'approved_awaiting_audio') AND track_id IS NULL)
        OR (status IN ('pending_audio_review', 'approved') AND track_id IS NOT NULL)
        OR status = 'rejected'),
    CHECK (status != 'rejected' OR track_id IS NULL
        OR (audio_reviewed_by IS NOT NULL AND audio_reviewed_at IS NOT NULL))
);
-- Terminal rejected attempts remain as history; a fresh attempt is allowed.
CREATE UNIQUE INDEX idx_submissions_contributor_source
    ON submissions(contributor_id, platform, source_id) WHERE status != 'rejected';
CREATE INDEX idx_submissions_queue ON submissions(status, created_at);
CREATE INDEX idx_submissions_source ON submissions(platform, source_id);

CREATE TRIGGER submissions_initial_state BEFORE INSERT ON submissions
WHEN NEW.status != 'draft' OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
    OR NEW.audio_reviewed_by IS NOT NULL OR NEW.audio_reviewed_at IS NOT NULL
    OR NEW.rejection_reason IS NOT NULL
    OR EXISTS (SELECT 1 FROM submissions WHERE id=NEW.id AND status != 'draft')
BEGIN
    SELECT RAISE(ABORT, 'submission must start as draft');
END;

CREATE TRIGGER submissions_transitions BEFORE UPDATE OF status ON submissions
WHEN NEW.status != OLD.status AND NOT (
    (OLD.status = 'draft' AND NEW.status = 'pending_review') OR
    (OLD.status = 'pending_review' AND NEW.status IN ('approved_awaiting_audio', 'rejected')) OR
    (OLD.status = 'approved_awaiting_audio' AND NEW.status = 'pending_audio_review') OR
    (OLD.status = 'pending_audio_review' AND NEW.status IN ('approved', 'rejected'))
)
BEGIN
    SELECT RAISE(ABORT, 'invalid submission transition');
END;

CREATE TRIGGER submissions_source_immutable BEFORE UPDATE ON submissions
WHEN NEW.id != OLD.id OR NEW.contributor_id != OLD.contributor_id OR NEW.platform != OLD.platform
    OR NEW.source_id != OLD.source_id OR NEW.source_url != OLD.source_url
    OR NEW.canonical_url != OLD.canonical_url
    OR (OLD.track_id IS NOT NULL AND NEW.track_id IS NOT OLD.track_id)
BEGIN
    SELECT RAISE(ABORT, 'submission identity and linked audio are immutable');
END;

CREATE TRIGGER submissions_link_audio BEFORE UPDATE OF track_id ON submissions
WHEN NEW.track_id IS NOT NULL AND OLD.track_id IS NULL
BEGIN
    SELECT CASE WHEN OLD.status != 'approved_awaiting_audio' OR NEW.status != 'pending_audio_review'
        THEN RAISE(ABORT, 'audio can only be attached after source acceptance') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM tracks WHERE id = NEW.track_id
        AND uploader_id = NEW.contributor_id AND status = 'pending'
    ) THEN RAISE(ABORT, 'linked audio must be pending and owned by contributor') END;
END;

CREATE TRIGGER submissions_admin_review BEFORE UPDATE ON submissions
BEGIN
    SELECT CASE WHEN (NEW.reviewed_by IS NOT OLD.reviewed_by OR NEW.reviewed_at IS NOT OLD.reviewed_at)
        AND NOT (OLD.status='pending_review' AND NEW.status IN ('approved_awaiting_audio','rejected')
            AND NEW.reviewed_at IS NOT NULL AND length(trim(NEW.reviewed_at)) > 0
            AND EXISTS (SELECT 1 FROM users WHERE id=NEW.reviewed_by AND role='admin'))
        THEN RAISE(ABORT, 'initial review must record an admin decision') END;
    SELECT CASE WHEN (NEW.audio_reviewed_by IS NOT OLD.audio_reviewed_by
        OR NEW.audio_reviewed_at IS NOT OLD.audio_reviewed_at)
        AND NOT (OLD.status='pending_audio_review' AND NEW.status IN ('approved','rejected')
            AND NEW.audio_reviewed_at IS NOT NULL AND length(trim(NEW.audio_reviewed_at)) > 0
            AND EXISTS (SELECT 1 FROM users WHERE id=NEW.audio_reviewed_by AND role='admin'))
        THEN RAISE(ABORT, 'audio review must record a final admin decision') END;
    SELECT CASE WHEN NEW.rejection_reason IS NOT OLD.rejection_reason
        AND NOT (OLD.status IN ('pending_review','pending_audio_review') AND NEW.status='rejected')
        THEN RAISE(ABORT, 'rejection reason belongs to a rejection decision') END;
    SELECT CASE WHEN OLD.status='pending_audio_review' AND NEW.status='approved'
        AND NOT EXISTS (SELECT 1 FROM tracks WHERE id=NEW.track_id AND status='pending')
        THEN RAISE(ABORT, 'audio approval requires pending audio') END;
END;

-- Drafts may be discarded; submitted/reviewed history is retained for V1.
CREATE TRIGGER submissions_delete_history BEFORE DELETE ON submissions
WHEN OLD.status != 'draft' OR OLD.track_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'submitted history cannot be deleted');
END;

-- REPLACE may bypass DELETE triggers with recursive_triggers off. Do not let
-- a conflicting insert replace a submitted active attempt or its history.
CREATE TRIGGER submissions_no_replace BEFORE INSERT ON submissions
WHEN EXISTS (SELECT 1 FROM submissions WHERE status != 'draft' AND (
    id=NEW.id OR (status != 'rejected' AND contributor_id=NEW.contributor_id
        AND platform=NEW.platform AND source_id=NEW.source_id)))
BEGIN
    SELECT RAISE(ABORT, 'submitted history cannot be replaced');
END;

CREATE TRIGGER submitted_audio_immutable BEFORE UPDATE ON tracks
WHEN EXISTS (SELECT 1 FROM submissions WHERE track_id=OLD.id) AND (
    NEW.id IS NOT OLD.id OR NEW.uploader_id IS NOT OLD.uploader_id
    OR NEW.file_path IS NOT OLD.file_path OR NEW.file_size IS NOT OLD.file_size
    OR NEW.mime_type IS NOT OLD.mime_type OR NEW.original_filename IS NOT OLD.original_filename
    OR NEW.duration_seconds IS NOT OLD.duration_seconds)
BEGIN
    SELECT RAISE(ABORT, 'attached audio identity is immutable');
END;

CREATE TRIGGER submitted_audio_no_replace BEFORE INSERT ON tracks
WHEN EXISTS (SELECT 1 FROM submissions WHERE track_id=NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'attached audio cannot be replaced');
END;

CREATE TRIGGER submissions_reject_audio AFTER UPDATE OF status ON submissions
WHEN OLD.status='pending_audio_review' AND NEW.status='rejected'
BEGIN
    UPDATE tracks SET status='rejected', reviewed_by=NEW.audio_reviewed_by,
        reviewed_at=NEW.audio_reviewed_at, rejection_reason=NEW.rejection_reason,
        updated_at=CURRENT_TIMESTAMP WHERE id=NEW.track_id;
END;

-- Legacy tracks have no submissions and retain their existing behavior.
-- Future service approves the submission and then its track in one transaction.
CREATE TRIGGER submitted_track_approval BEFORE UPDATE OF status ON tracks
WHEN EXISTS (
    SELECT 1 FROM submissions WHERE track_id = OLD.id AND (
        (NEW.status='approved' AND status != 'approved')
        OR (status='rejected' AND NEW.status != 'rejected')
        OR (status='pending_audio_review' AND NEW.status != 'pending'))
)
BEGIN
    SELECT RAISE(ABORT, 'explicit final audio review required');
END;
