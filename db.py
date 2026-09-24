import sqlite3
import os
from config import DB_PATH


def get_db():
    """Ouvre une connexion SQLite avec row_factory en dict-like."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Crée les tables si elles n'existent pas (à lancer une fois)."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    conn = get_db()
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print(f"[DB] Initialisée → {DB_PATH}")


if __name__ == "__main__":
    init_db()
