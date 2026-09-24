-- ═════════════════════════════════════════════
-- SCHEMA — radio live collaborative
-- ═════════════════════════════════════════════

PRAGMA foreign_keys = ON;

-- ── Utilisateurs ──
CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    email                 TEXT UNIQUE NOT NULL,
    password_hash         TEXT,                 -- nullable : comptes Google-only
    google_id             TEXT UNIQUE,          -- nullable : OAuth Google
    is_verified           BOOLEAN NOT NULL DEFAULT 0,
    verification_token    TEXT,
    verification_sent_at  DATETIME,
    display_name          TEXT,
    role                  TEXT NOT NULL DEFAULT 'contributor'
                          CHECK (role IN ('contributor','admin')),
    invited_by            INTEGER,              -- qui a parrainé ce compte
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invited_by) REFERENCES users(id)
);

-- ── Invitations (démarchage créateurs) ──
CREATE TABLE IF NOT EXISTS invitations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token        TEXT UNIQUE NOT NULL,
    email        TEXT,                          -- destinataire prévu (nullable)
    invited_by   INTEGER NOT NULL,
    used_at      DATETIME,
    expires_at   DATETIME NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invited_by) REFERENCES users(id)
);

-- ── Genres (référentiel) ──
CREATE TABLE IF NOT EXISTS genres (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    slug   TEXT UNIQUE NOT NULL,
    label  TEXT NOT NULL,
    sort   INTEGER DEFAULT 0
);

INSERT OR IGNORE INTO genres (slug, label, sort) VALUES
    ('jingle',      'Jingle',      10),
    ('boogie_funk', 'Boogie Funk', 20),
    ('deep_house',  'Deep House',  30);

-- ── Tracks (MP3 importés) ──
CREATE TABLE IF NOT EXISTS tracks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    uploader_id        INTEGER NOT NULL,

    -- fichier
    file_path          TEXT NOT NULL,
    file_size          INTEGER,
    mime_type          TEXT DEFAULT 'audio/mpeg',
    original_filename  TEXT,

    -- métadonnées (remplies à l'upload)
    title              TEXT NOT NULL,
    artist             TEXT,
    album              TEXT,
    year               INTEGER,
    genre_id           INTEGER NOT NULL REFERENCES genres(id),
    duration_seconds   REAL,
    meta_path          TEXT,          -- image OG mise en cache (relative à storage/meta/)
    source_url         TEXT,          -- lien YouTube/Spotify du créateur

    -- workflow de validation
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected')),
    reviewed_by        INTEGER,
    reviewed_at        DATETIME,
    rejection_reason   TEXT,

    -- programmation
    play_order         INTEGER,                 -- ordre dans la playlist (nullable = aléatoire)

    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (uploader_id) REFERENCES users(id),
    FOREIGN KEY (reviewed_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_play_order ON tracks(play_order);

-- ── Journal des diffusions (pour l'historique + podcast futur) ──
CREATE TABLE IF NOT EXISTS plays (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id    INTEGER NOT NULL,
    started_at  DATETIME NOT NULL,
    ended_at    DATETIME,
    FOREIGN KEY (track_id) REFERENCES tracks(id)
);

CREATE INDEX IF NOT EXISTS idx_plays_started ON plays(started_at DESC);

