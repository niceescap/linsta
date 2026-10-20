"""Editorial metadata in the private catalogue only. No audio or provider writes."""
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

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

MIGRATION_V3 = """BEGIN IMMEDIATE;
ALTER TABLE private_tracks ADD COLUMN cover_path TEXT;
ALTER TABLE private_tracks ADD COLUMN source_platform TEXT CHECK(source_platform IS NULL OR source_platform IN ('youtube','spotify','soundcloud','autre'));
ALTER TABLE private_tracks ADD COLUMN source_url TEXT;
ALTER TABLE private_tracks ADD COLUMN internal_note TEXT;
PRAGMA user_version=3;
COMMIT;"""
PLATFORMS = {'youtube', 'spotify', 'soundcloud', 'autre'}
_UNSET = object()


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
        if version == 3:
            return
        if version == 1:
            conn.executescript(MIGRATION)
            version = 2
        if version == 2:
            conn.executescript(MIGRATION_V3)
            return
        raise ValueError('La migration éditoriale exige le schéma privé v1, v2 ou v3.')
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


def change(root, tid, revision, action, *, title=None, artist=None, genre='', year=None,
           source_platform=_UNSET, source_url=_UNSET, internal_note=_UNSET, cover_path=None,
           reason=None, confirm=None):
    if type(tid) is not int or tid < 1 or type(revision) is not int or revision < 0:
        raise ValueError('Identifiant ou révision invalide.')
    conn = connect(root, write=True)
    try:
        if conn.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise ValueError('Migration éditoriale v3 nécessaire.')
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
                if source_platform is not _UNSET:
                    if source_platform not in (None, '') and (not isinstance(source_platform, str)
                            or source_platform not in PLATFORMS):
                        raise ValueError('Plateforme source invalide.')
                    values['source_platform'] = source_platform or None
                if source_url is not _UNSET:
                    if source_url in (None, ''):
                        values['source_url'] = None
                    elif (not isinstance(source_url, str) or len(source_url) > 2048
                          or any(ord(c) < 32 or ord(c) == 127 for c in source_url)):
                        raise ValueError('URL source invalide (2048 caractères maximum).')
                    else:
                        try:
                            parsed = urlsplit(source_url.strip())
                            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                                raise ValueError
                            parsed.port
                        except ValueError:
                            raise ValueError('URL source HTTP ou HTTPS invalide.') from None
                        values['source_url'] = source_url.strip()
                if internal_note is not _UNSET:
                    if (not isinstance(internal_note, str) or len(internal_note) > 2000
                            or any(ord(c) < 32 and c not in '\n\r\t' or ord(c) == 127 for c in internal_note)):
                        raise ValueError('Note interne invalide (2000 caractères maximum).')
                    values['internal_note'] = internal_note.strip() or None
                if cover_path is not None:
                    if (not isinstance(cover_path, str)
                            or not re.fullmatch(r'covers/digger-cover-[0-9a-f]{32}\.(?:jpg|png)', cover_path)):
                        raise ValueError('Chemin de cover invalide.')
                    values['cover_path'] = cover_path
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
            conn.execute('''UPDATE private_tracks SET title=?,artist=?,genre=?,year=?,cover_path=?,
                source_platform=?,source_url=?,internal_note=?,enabled=?,
                removed_at=CASE WHEN ? IN ('remove','reject') THEN COALESCE(removed_at,CURRENT_TIMESTAMP) ELSE ? END,
                rejection_reason=?,updated_at=CURRENT_TIMESTAMP,revision=revision+1 WHERE id=?''',
                (values['title'],values['artist'],values['genre'],values['year'],values['cover_path'],
                 values['source_platform'],values['source_url'],values['internal_note'],values['enabled'],
                 action,values['removed_at'],values['rejection_reason'],tid))
            after = dict(conn.execute('SELECT * FROM private_tracks WHERE id=?', (tid,)).fetchone())
            conn.execute('''INSERT INTO private_catalogue_events(track_id,action,before_json,after_json)
                VALUES (?,?,?,?)''', (tid,action,json.dumps(before),json.dumps(after)))
    finally:
        conn.close()
