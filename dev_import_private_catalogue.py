"""Explicit DEV-only snapshot of already-authorized private media. Source read-only."""
import json
import os
from pathlib import Path
import sqlite3
import shutil

import dev_runtime
import private_radio
import preview_editorial
import preview_jingles
from audio_storage import checked_open

SOURCE = Path('/home/jean/digger/instance/demo/private-radio')
DESTINATION = dev_runtime.ROOT / 'private-radio'


def copy_verified(source_root, name, target_root, *, digest, size):
    target_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = target_root / name
    if target.exists():
        with checked_open(target_root, name, sha256=digest, size=size)[0] as stream:
            while stream.read(1024 * 1024):
                pass
        return
    with checked_open(source_root, name, sha256=digest, size=size)[0] as stream:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output:
            shutil.copyfileobj(stream, output)
    with checked_open(target_root, name, sha256=digest, size=size)[0] as stream:
        while stream.read(1024 * 1024):
            pass


def run():
    dev_runtime.check_paths()
    old_mask = os.umask(0o077)
    try:
        with private_radio.catalogue_lock(DESTINATION):
            with sqlite3.connect((SOURCE / 'catalogue.sqlite').as_uri()+'?mode=ro', uri=True) as src:
                src.row_factory = sqlite3.Row
                rows = [dict(r) for r in src.execute('SELECT * FROM private_tracks ORDER BY position')]
            rows = [r for r in rows if preview_editorial.eligible(r)]
            jingles = [r for r in preview_jingles.scan(SOURCE) if r['valid']]
            conn = preview_editorial.connect(DESTINATION, write=True)
            try:
                existing = {r['sha256']:r['id'] for r in conn.execute('SELECT id,sha256 FROM private_tracks')}
                position = conn.execute('SELECT coalesce(max(position),-1)+1 FROM private_tracks').fetchone()[0]
                imported = []
                with conn:
                    for row in rows:
                        private_radio.validate_file(SOURCE,row['filename'],digest=row['sha256'],size=row['size'])
                        copy_verified(SOURCE,row['filename'],DESTINATION,digest=row['sha256'],size=row['size'])
                        if row.get('cover_path'):
                            # Use the same validated private-cover reader as the web; no path guessing.
                            from preview_radio_web import private_cover
                            cover = private_cover(SOURCE,row)
                            if not cover:
                                raise ValueError('Source cover unavailable')
                            relative = Path(row['cover_path'])
                            if relative.is_absolute() or '..' in relative.parts:
                                raise ValueError('Unsafe cover path')
                            folder = DESTINATION / relative.parent
                            folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                            target = folder/relative.name
                            if target.exists():
                                if target.read_bytes()!=cover[0]: raise ValueError('Cover conflict')
                            else:
                                with target.open('xb') as output: output.write(cover[0])
                        if row['sha256'] in existing:
                            continue
                        values = {k:v for k,v in row.items() if k not in ('id','position')}
                        values['position'] = position
                        columns = list(values)
                        cursor = conn.execute('INSERT INTO private_tracks ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')', [values[k] for k in columns])
                        imported.append({'source_id':row['id'],'dev_id':cursor.lastrowid,'title':row['title']})
                        existing[row['sha256']] = cursor.lastrowid
                        position += 1
                    # Preserve the three fixtures and their authorizations, but remove tones from rotation.
                    conn.execute("UPDATE private_tracks SET enabled=0 WHERE artist='Digger DEV generated tone' AND filename IN ('synthetic-220hz.mp3','synthetic-330hz.mp3','synthetic-440hz.mp3')")
                    for item in jingles:
                        copy_verified(preview_jingles.directory(SOURCE),item['name'],preview_jingles.directory(DESTINATION),digest=item['sha256'],size=item['size'])
                report = dict(source=str(SOURCE), imported=imported, source_active=len(rows), jingles=len(jingles),
                              note='Source read-only; copied validation fields unchanged; synthetic fixtures preserved but disabled')
                (dev_runtime.ROOT/'catalogue-import.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print(json.dumps(report,ensure_ascii=False))
            finally:
                conn.close()
        dev_runtime.check_paths()
    finally:
        os.umask(old_mask)


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',required=True)
    parser.parse_args()
    run()
