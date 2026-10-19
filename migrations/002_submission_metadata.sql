-- Additive provider snapshot fields. Never alter editorial or radio state.
ALTER TABLE submissions ADD COLUMN source_duration_seconds REAL
    CHECK (source_duration_seconds IS NULL OR
        (typeof(source_duration_seconds) IN ('integer','real')
         AND source_duration_seconds >= 0 AND source_duration_seconds <= 1.7976931348623157e308));
ALTER TABLE submissions ADD COLUMN source_album TEXT;
ALTER TABLE submissions ADD COLUMN source_description TEXT;
ALTER TABLE submissions ADD COLUMN metadata_generation INTEGER NOT NULL DEFAULT 0
    CHECK (typeof(metadata_generation)='integer' AND metadata_generation >= 0);

CREATE TABLE submission_metadata_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id),
    generation INTEGER NOT NULL CHECK (typeof(generation)='integer' AND generation > 0),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','available','partial','failed','superseded')),
    error_code TEXT CHECK (error_code IN (
        'disabled','invalid_configuration','missing_credentials','missing_api_key',
        'access_denied','not_found','quota_exceeded','rate_limited','timeout',
        'network_error','service_unavailable','http_error','invalid_response',
        'source_relinked','token_expired')),
    retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0,1)),
    retry_after_at TEXT,
    UNIQUE (submission_id, generation),
    CHECK ((status='pending' AND finished_at IS NULL) OR
           (status!='pending' AND finished_at IS NOT NULL AND length(trim(finished_at)) > 0)),
    CHECK ((status='failed' AND error_code IS NOT NULL) OR
           (status='superseded') OR (status IN ('pending','available','partial') AND error_code IS NULL)),
    CHECK (retryable=0 OR error_code IS NOT NULL),
    CHECK (retry_after_at IS NULL OR (retryable=1 AND error_code IS NOT NULL))
);
CREATE INDEX idx_metadata_attempt_history ON submission_metadata_attempts(submission_id, id DESC);

CREATE TRIGGER metadata_attempt_initial BEFORE INSERT ON submission_metadata_attempts
WHEN NEW.status!='pending' OR NEW.error_code IS NOT NULL OR NEW.retryable!=0
    OR NEW.retry_after_at IS NOT NULL OR NEW.finished_at IS NOT NULL
    OR NOT EXISTS (SELECT 1 FROM submissions WHERE id=NEW.submission_id
        AND metadata_generation=NEW.generation AND status!='rejected')
BEGIN
    SELECT RAISE(ABORT, 'attempt must start pending at current generation');
END;

CREATE TRIGGER metadata_attempt_final BEFORE UPDATE ON submission_metadata_attempts
WHEN OLD.status!='pending' OR NEW.status='pending'
    OR NEW.id IS NOT OLD.id OR NEW.submission_id IS NOT OLD.submission_id
    OR NEW.generation IS NOT OLD.generation OR NEW.started_at IS NOT OLD.started_at
BEGIN
    SELECT RAISE(ABORT, 'attempt history is immutable except pending completion');
END;

CREATE TRIGGER metadata_attempt_no_delete BEFORE DELETE ON submission_metadata_attempts
BEGIN
    SELECT RAISE(ABORT, 'metadata history cannot be deleted');
END;

CREATE TRIGGER metadata_attempt_no_replace BEFORE INSERT ON submission_metadata_attempts
WHEN EXISTS (SELECT 1 FROM submission_metadata_attempts WHERE id=NEW.id
    OR (submission_id=NEW.submission_id AND generation=NEW.generation))
BEGIN
    SELECT RAISE(ABORT, 'metadata history cannot be replaced');
END;

CREATE TRIGGER metadata_generation_monotonic BEFORE UPDATE OF metadata_generation ON submissions
WHEN NEW.metadata_generation < OLD.metadata_generation
BEGIN
    SELECT RAISE(ABORT, 'metadata generation cannot decrease');
END;
