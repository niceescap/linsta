"""Read-only full audio analysis of the DEV catalogue and existing jingles."""
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import preview_editorial
import preview_jingles
import dev_runtime


def run():
    os.umask(0o077)
    root=dev_runtime.ROOT/'private-radio'
    items=[dict(name=r['filename'],kind='track',duration=r['duration']) for r in preview_editorial.listing(root) if preview_editorial.eligible(r)]
    jingles=preview_jingles.scan(root)
    items += [dict(name=r['name'],kind='jingle',duration=r['duration']) for r in jingles if r['valid']]
    for item in items:
        path=(root if item['kind']=='track' else preview_jingles.directory(root))/item['name']
        result=subprocess.run(['nice','-n','15','ffmpeg','-hide_banner','-nostdin','-nostats','-threads','1',
            '-filter_threads','1','-filter_complex_threads','1','-i',str(path),'-map','0:a:0','-vn',
            '-af','ebur128=peak=sample','-f','null','-'],capture_output=True,text=True,timeout=90)
        if result.returncode: raise RuntimeError('Audio decode failed: '+item['name'])
        summary=result.stderr.rsplit('Summary:',1)[-1]
        item['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        item['integrated_lufs']=float(re.search(r'I:\s+(-?[\d.]+) LUFS',summary)[1])
        item['peak_dbfs']=float(re.search(r'Peak:\s+(-?[\d.]+) dBFS',summary)[1])
    report=dict(items=items,decoded=len(items),jingles=len(jingles))
    (dev_runtime.ROOT/'volet02-levels.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(dict(decoded=len(items),jingles=[i for i in items if i['kind']=='jingle'],track_lufs_range=[min(i['integrated_lufs'] for i in items if i['kind']=='track'),max(i['integrated_lufs'] for i in items if i['kind']=='track')]),ensure_ascii=False))


if __name__=='__main__': run()
