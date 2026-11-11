"""Explicit offline dev-only tones. No server, engine, real media or systemd."""
import json
import os
import sqlite3
import subprocess

import dev_runtime


def prepare():
    dev_runtime.check_paths()
    root=dev_runtime.ROOT/'private-radio'
    import private_radio
    with sqlite3.connect((root/'catalogue.sqlite').as_uri()+'?mode=ro',uri=True) as conn:
        if conn.execute('SELECT count(*) FROM private_tracks').fetchone()[0]:
            raise RuntimeError('Refusing to replace a nonempty catalogue')
    names=[f'synthetic-{frequency}hz.mp3' for frequency in (220,330,440)]
    manifest=root/'synthetic-playlist.json'
    if manifest.exists() or any((root/name).exists() for name in names):
        raise RuntimeError('Synthetic fixtures already exist; nothing overwritten')
    old=os.umask(0o077)
    try:
        for frequency,name in zip((220,330,440),names):
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin',
                '-n','-filter_threads','1','-filter_complex_threads','1','-f','lavfi',
                '-i',f'sine=frequency={frequency}:duration=20','-af',
                'volume=0.15,afade=t=in:d=0.1,afade=t=out:st=19.9:d=0.1',
                '-threads','1','-ac','2','-ar','44100','-codec:a','libmp3lame',str(root/name)],
                check=True,timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        manifest.write_text(json.dumps([dict(file=name,title=f'Synthetic {frequency} Hz',
            artist='Digger DEV generated tone') for frequency,name in zip((220,330,440),names)]))
        import private_radio_admin, preview_editorial
        private_radio_admin.migrate(root)
        preview_editorial.migrate(root)
        # These media were generated here; explicit admission grants private test only.
        private_radio.import_playlist(root,manifest,confirmed=True)
        dev_runtime.check_paths()
    finally:
        os.umask(old)
    print('Three synthetic MP3s admitted to the DEV private catalogue only.')


if __name__=='__main__':prepare()
