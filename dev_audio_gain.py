"""DEV-only measured constant gain; no source-file edits or dynamic compression."""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess


class MeasuredGain:
    def __init__(self, root, report, *, target=-18.0):
        self.root = Path(root).resolve()
        self.target = target
        self.measurements = {r['name']:r for r in json.loads(Path(report).read_text())['items']}
        self.cache = {}

    def __call__(self, supplied):
        path = Path(supplied)
        if path.is_symlink() or self.root not in path.resolve().parents:
            raise ValueError('Gain requires a private DEV source')
        stat = path.stat()
        key = (str(path),stat.st_size,stat.st_mtime_ns)
        if key in self.cache:
            return self.cache[key]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        item = self.measurements.get(path.name)
        if item is None or item.get('sha256') != digest:
            # New admin additions join the same catalogue and are measured once, not reimported.
            result = subprocess.run(['ffmpeg','-hide_banner','-nostdin','-nostats','-threads','1',
                '-filter_threads','1','-filter_complex_threads','1','-i',str(path),'-vn','-map','0:a:0',
                '-af','ebur128=peak=sample','-f','null','-'],capture_output=True,text=True,timeout=90,check=True)
            summary = result.stderr.rsplit('Summary:',1)[-1]
            item = dict(integrated_lufs=float(re.search(r'I:\s+(-?[\d.]+) LUFS',summary)[1]),
                        peak_dbfs=float(re.search(r'Peak:\s+(-?[\d.]+) dBFS',summary)[1]),sha256=digest)
            self.measurements[path.name] = item
        gain = min(self.target-item['integrated_lufs'], -1.5-item['peak_dbfs'])
        if not math.isfinite(gain) or not -30 <= gain <= 12:
            raise ValueError('Unreasonable DEV gain')
        self.cache[key] = gain
        print('[dev-gain] '+path.name+' '+format(gain,'.2f')+' dB',flush=True)
        return gain
