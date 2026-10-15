import sqlite3
import os
import hashlib
import re
from pathlib import Path


def _configured_path():
    # Keep migration tests/tools independent of application secrets and imports.
    from config import DB_PATH
    return DB_PATH


def get_db(path=None):
    """Ouvre une connexion SQLite avec row_factory en dict-like."""
    path = os.fspath(path if path is not None else _configured_path())
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migration_inventory(directory):
    directory = Path(directory)
    if not directory.is_dir() or not (directory / '001_submissions.sql').is_file():
        raise ValueError('Required migration resources missing: 001_submissions.sql')
    inventory = {}
    for file in directory.glob('*.sql'):
        match = re.fullmatch(r'([0-9]+)_.+\.sql', file.name)
        if not match or int(match[1]) < 1:
            raise ValueError(f'Invalid migration filename: {file.name}')
        version = int(match[1])
        if version in inventory:
            raise ValueError(f'Duplicate migration version: {version}')
        content = file.read_bytes()
        inventory[version] = (file.name, hashlib.sha256(content).hexdigest(), content.decode('utf-8'))
    return dict(sorted(inventory.items()))


def migrate_db(path=None, *, migrations_dir=None):
    """Explicit, additive upgrades. Never called by get_db() or the worker.

    The entire upgrade and its ledger entries commit together or roll back.
    Back up an existing database before invoking this maintenance operation.
    """
    inventory = _migration_inventory(migrations_dir if migrations_dir is not None
                                     else Path(__file__).with_name('migrations'))
    conn = get_db(path)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone():
                if 'checksum' not in {r['name'] for r in conn.execute('PRAGMA table_info(schema_migrations)')}:
                    raise ValueError('Unverified migration ledger: checksum missing; explicit reconciliation required')
            else:
                conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
            applied = {r['version']: r for r in conn.execute('SELECT * FROM schema_migrations')}
            for version, row in applied.items():
                if version not in inventory:
                    raise ValueError(f'Applied migration resource missing: {version}')
                name, checksum, _ = inventory[version]
                if row['name'] != name or row['checksum'] != checksum:
                    raise ValueError(f'Applied migration changed: {version}')
            for version, (name, checksum, sql) in inventory.items():
                if version in applied:
                    continue
                if applied and version < max(applied):
                    raise ValueError(f'Out-of-order migration: {version}')
                # executescript would implicitly commit; execute complete SQL
                # statements separately, including multi-line trigger bodies.
                statement = ''
                for line in sql.splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        conn.execute(statement)
                        statement = ''
                if statement.strip():
                    raise ValueError(f'Incomplete SQL in {name}')
                conn.execute('INSERT INTO schema_migrations(version,name,checksum) VALUES (?,?,?)',
                             (version, name, checksum))
    finally:
        conn.close()


def finalize_audio_review(conn, submission_id, reviewer_id, *, approve, reason=None):
    """Atomic final decision on a dedicated, idle connection.

    Caller must authorize the authenticated admin; SQL validates the recorded
    reviewer role, not the identity of the caller. Any failure rolls back both rows.
    """
    if conn.in_transaction:
        raise ValueError('Final audio review requires an idle connection')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT track_id FROM submissions WHERE id=? AND status='pending_audio_review'",
                           (submission_id,)).fetchone()
        if row is None:
            raise ValueError('Submission is not awaiting audio review')
        conn.execute("""UPDATE submissions SET status=?, audio_reviewed_by=?,
            audio_reviewed_at=CURRENT_TIMESTAMP, rejection_reason=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?""", ('approved' if approve else 'rejected', reviewer_id,
                            None if approve else reason, submission_id))
        if approve:
            changed = conn.execute("""UPDATE tracks SET status='approved', reviewed_by=?,
                reviewed_at=CURRENT_TIMESTAMP, rejection_reason=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='pending'""", (reviewer_id, row['track_id']))
            if changed.rowcount != 1:
                raise ValueError('Linked track is not pending')
        # Rejection is synchronized by the submission trigger, even for direct SQL.


def init_db(path=None):
    """Crée les tables si elles n'existent pas (à lancer une fois)."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    conn = get_db(path)
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
    finally:
        conn.close()
    migrate_db(path)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Explicit SQLite initialization / migration")
    parser.add_argument("action", nargs="?", choices=("init", "migrate"), default="init")
    parser.add_argument("--database", help="Explicit database path (defaults to application config)")
    args = parser.parse_args()
    (init_db if args.action == "init" else migrate_db)(args.database)
