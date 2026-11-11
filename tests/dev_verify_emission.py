"""Verify real emitted DEV boundaries, single encoder and captured AAC segments."""
import json
import os
from pathlib import Path
import re
import subprocess
import dev_runtime


def run():
    os.umask(0o077)
    root=dev_runtime.ROOT/'private-radio'
    out=dev_runtime.ROOT/'volet02-validation'
    report=json.loads((out/'report.json').read_text())
    if report['status']!='passed':raise RuntimeError('Real browser verification did not pass')
    timeline=json.loads((root/'hls/timeline.json').read_text())
    boundaries=[]
    for before,after in zip(timeline['tracks'],timeline['tracks'][1:]):
        duration=before.get('duration')
        gap=after['offset']-before['offset']-duration if duration else None
        entry=dict(before=before['type'],after=after['type'],offset=after['offset'],extra_gap_seconds=gap)
        if gap is not None and gap>.6:raise RuntimeError('Excessive PCM source gap: '+str(entry))
        boundaries.append(entry)
    names=report['segments']
    numbers=[int(re.fullmatch(r'seg_(\d+)\.ts',n)[1]) for n in names]
    if any(b!=a+1 for a,b in zip(numbers,numbers[1:])):raise RuntimeError('Captured segment sequence has a gap')
    # Decode the actual captured live segments, not source MP3s or browser mocks.
    manifest=out/'captured.txt'
    manifest.write_text(''.join("file '"+name+"'\n" for name in names))
    result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','1',
        '-f','concat','-safe','1','-i',str(manifest),'-map','0:a:0','-f','null','-'],capture_output=True,text=True,timeout=60)
    if result.returncode:raise RuntimeError('Captured HLS cannot decode: '+result.stderr[-600:])
    # Only inspect the current run, after the previous clean stop.
    log=(dev_runtime.ROOT/'volet02-radio.log').read_text().rsplit('[continuous] started;',1)[-1]
    pids=re.findall(r'encoder PID=(\d+)',log)
    if len(set(pids))!=1:raise RuntimeError('Encoder was unexpectedly replaced')
    if 'temporary silence' in log or 'resumed after' in log:raise RuntimeError('PCM starvation recorded during the live run')
    evidence=dict(status='passed',boundaries=boundaries,captured_segments_decoded=len(names),
                  segment_sequence='contiguous',encoder_pid=int(pids[0]),pcm_starvation=False,
                  note='Automatic real HLS decode; subjective Safari listening still required')
    (out/'emission.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps(evidence))


if __name__=='__main__':run()
