"""Editorial metadata in the private catalogue only. No audio or provider writes."""
import json
import sqlite3
from pathlib import Path

REASONS = {
    'qualite_audio_insuffisante': 'Qualité audio insuffisante',
    'doublon': 'Doublon', 'droits_incomplets': 'Droits incomplets',
    'contenu_non_adapte': 'Contenu non adapté', 'autre': 'Autre',
}
MIGRATION = """BEGIN IMMEDIATE;
ALTER TABLE private_tracks ADD COLUMN genre TEXT NOT NULL DEFAULT '';
ALTER TABLE private_tracks ADD COLUMN year INTEGER CHECK(year IS NULL OR year BETWEEN 1000 AND 9999);
ALTER TABLE private_tracks ADD COLUMN rejection_reason TEXT CHECK(rejection_reason IS NULL OR rejection_reason IN ('qualite_audio_insuffisante','doublon','droits_incomplets','contenu_non_adapte','autre'));
ALTER TABLE private_tracks ADD COLUMN updated_at TEXT;
PRAGMA user_version=2;
COMMIT;"""


def connect(root, *, write=False):
    from private_radio import safe_root
    root = safe_root(Path(root))
    conn = sqlite3.connect((root/'catalogue.sqlite').as_uri()+('?mode=rw' if write else '?mode=ro'), uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def migrate(root):
    conn = connect(root, write=True)
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 2:
            return
        if version != 1:
            raise ValueError('La migration éditoriale exige le schéma privé v1.')
        conn.executescript(MIGRATION)
    finally:
        conn.close()


def status(row):
    if row.get('rejection_reason'):
        return 'rejected'
    return 'active' if row.get('enabled', 1) and row.get('removed_at') is None else 'removed'


def eligible(row):
    return bool(row.get('private_rights_confirmed')) and status(row) == 'active'


def listing(root, *, query='', state='all'):
    if state not in ('all', 'active', 'removed', 'rejected'):
        raise ValueError('Filtre invalide.')
    conn = connect(root)
    try:
        rows = [dict(r) for r in conn.execute('SELECT * FROM private_tracks ORDER BY position')]
    finally:
        conn.close()
    needle = query.strip().casefold()
    return [r for r in rows if (state == 'all' or status(r) == state)
            and (not needle or needle in r['title'].casefold() or needle in r['artist'].casefold())]


def change(root, tid, revision, action, *, title=None, artist=None, genre='', year=None, reason=None, confirm=None):
    if type(tid) is not int or tid < 1 or type(revision) is not int or revision < 0:
        raise ValueError('Identifiant ou révision invalide.')
    conn = connect(root, write=True)
    try:
        if conn.execute('PRAGMA user_version').fetchone()[0] != 2:
            raise ValueError('Migration éditoriale v2 nécessaire.')
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            found = conn.execute('SELECT * FROM private_tracks WHERE id=?', (tid,)).fetchone()
            if found is None:
                raise ValueError('Morceau introuvable.')
            before = dict(found)
            if before['revision'] != revision:
                raise ValueError('Page périmée : rechargez avant de modifier.')
            values = dict(before)
            if action == 'metadata':
                for key, value in (('title', title), ('artist', artist), ('genre', genre)):
                    if not isinstance(value, str) or len(value.strip()) > 256 or any(ord(c)<32 or ord(c)==127 for c in value):
                        raise ValueError('Métadonnées invalides : 256 caractères maximum, sans contrôle.')
                    if key != 'genre' and not value.strip():
                        raise ValueError('Titre et artiste requis.')
                    values[key] = value.strip()
                if year in ('', None):
                    values['year'] = None
                elif str(year).isdigit() and 1000 <= int(year) <= 9999:
                    values['year'] = int(year)
                else:
                    raise ValueError('Année invalide (1000–9999).')
            elif action in ('remove', 'reject'):
                if confirm != str(tid):
                    raise ValueError('Confirmation requise.')
                if action == 'reject':
                    if reason not in REASONS:
                        raise ValueError('Motif de refus obligatoire.')
                    values['rejection_reason'] = reason
                elif before['rejection_reason']:
                    raise ValueError('Un track refusé ne peut pas être retiré à nouveau.')
                values['enabled'] = 0
            elif action == 'restore':
                if before['rejection_reason']:
                    raise ValueError('Un track refusé ne peut pas être remis en diffusion.')
                values.update(enabled=1, removed_at=None)
            else:
                raise ValueError('Action inconnue.')
            conn.execute('''UPDATE private_tracks SET title=?,artist=?,genre=?,year=?,enabled=?,
                removed_at=CASE WHEN ? IN ('remove','reject') THEN COALESCE(removed_at,CURRENT_TIMESTAMP) ELSE ? END,
                rejection_reason=?,updated_at=CURRENT_TIMESTAMP,revision=revision+1 WHERE id=?''',
                (values['title'],values['artist'],values['genre'],values['year'],values['enabled'],
                 action,values['removed_at'],values['rejection_reason'],tid))
            after = dict(conn.execute('SELECT * FROM private_tracks WHERE id=?', (tid,)).fetchone())
            conn.execute('''INSERT INTO private_catalogue_events(track_id,action,before_json,after_json)
                VALUES (?,?,?,?)''', (tid,action,json.dumps(before),json.dumps(after)))
    finally:
        conn.close()
