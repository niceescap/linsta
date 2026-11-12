"""Real DEV-only endurance run; invoked under an independent GNU timeout."""
import json
import fcntl
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

import dev_runtime
import private_radio
from preview_radio_web import health

REPO = Path(__file__).resolve().parent
ROOT = dev_runtime.ROOT / 'private-radio'
OUT = dev_runtime.ROOT / 'endurance'


def database():
    return sqlite3.connect((ROOT / 'catalogue.sqlite').as_uri() + '?mode=ro', uri=True)


def members(group):
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                result.append((int(entry.name), int(fields[11]) + int(fields[12]),
                               int(fields[21]) * os.sysconf('SC_PAGE_SIZE')))
        except (OSError, ValueError, IndexError):
            pass
    return result


def preflight():
    dev_runtime.check_paths()
    if REPO != Path('/home/jean/digger-dev'):
        raise RuntimeError('Unexpected checkout')
    if dev_runtime.DevController().inspect()['state'] != 'inactive':
        raise RuntimeError('DEV engine not demonstrably inactive')
    with database() as conn:
        rows = conn.execute('SELECT filename,sha256,size FROM private_tracks').fetchall()
    expected = {f'synthetic-{hz}hz.mp3' for hz in (220, 330, 440)}
    if len(rows) != 3 or {row[0] for row in rows} != expected:
        raise RuntimeError('Exactly three synthetic MP3s required')
    for name, digest, size in rows:
        if private_radio.validate_file(ROOT, name)[:2] != (digest, size):
            raise RuntimeError('Synthetic file validation failed')
    if len(private_radio.tracks(ROOT)) != 3:
        raise RuntimeError('Three eligible tracks required')
    if shutil.disk_usage(ROOT).free < 1024**3:
        raise RuntimeError('At least 1 GiB free required')
    if (ROOT / 'jingles').exists() and any((ROOT / 'jingles').iterdir()):
        raise RuntimeError('No jingles allowed for synthetic-only test')


def run():
    os.umask(0o077)
    OUT.mkdir(mode=0o700, exist_ok=True)
    lock = (OUT / 'run.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    report = {'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'status': 'preflight', 'duration_seconds': 0, 'samples': 0,
              'peak_rss_bytes': 0, 'peak_cpu_percent': 0, 'peak_storage_bytes': 0}
    child = None
    log = None
    def save():
        temporary = OUT / 'report.tmp'
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(OUT / 'report.json')
    def interrupted(signum, frame):
        raise RuntimeError('External deadline or manual interruption')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    start = time.monotonic()
    try:
        preflight()
        with database() as conn:
            baseline = conn.execute('SELECT coalesce(max(id),0) FROM private_plays').fetchone()[0]
        log = (OUT / 'engine.log').open('w')
        child = subprocess.Popen([sys.executable, '-B', '-m', 'dev_radio', '--mode', 'continuous'],
            cwd=REPO, env={'PATH': os.defpath, 'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        report.update(status='running', engine_pid=child.pid, supervisor_pid=os.getpid())
        last_segments = None
        last_progress = start
        previous_cpu = 0
        previous_time = start
        log_offset = 0
        with (OUT / 'samples.jsonl').open('w') as samples:
            while time.monotonic() - start < 7160:
                now = time.monotonic()
                if child.poll() is not None:
                    raise RuntimeError('Engine exited unexpectedly')
                state = health(ROOT, dev_runtime.DevController())
                segments = state.get('segments')
                if state['available'] and segments != last_segments:
                    last_progress, last_segments = now, segments
                if now - last_progress > 35:
                    raise RuntimeError('HLS stalled or unavailable for 35 seconds')
                if state['available'] and report['samples'] % 12 == 0:
                    subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                        '-show_entries', 'stream=codec_name', '-of', 'json',
                        str(ROOT / 'hls' / segments[-1])], check=True, timeout=8,
                        stdout=subprocess.DEVNULL, stderr=log)
                processes = members(child.pid)
                rss = sum(item[2] for item in processes)
                ticks = sum(item[1] for item in processes)
                cpu = max(0, ticks - previous_cpu) / os.sysconf('SC_CLK_TCK') / (now - previous_time) * 100
                previous_cpu, previous_time = ticks, now
                storage = sum(p.stat().st_size for p in dev_runtime.ROOT.rglob('*') if p.is_file())
                free = shutil.disk_usage(ROOT).free
                with database() as conn:
                    plays = conn.execute('SELECT track_id,started_at,ended_at FROM private_plays WHERE id>? ORDER BY id', (baseline,)).fetchall()
                if now - start > 60:
                    if len(plays) < 2:
                        raise RuntimeError('No music transitions')
                    # Each 20-second source must finish within a generous 50 seconds.
                    with database() as conn:
                        age = conn.execute("SELECT coalesce(max(strftime('%s','now')-strftime('%s',started_at)),0) FROM private_plays WHERE id>? AND ended_at IS NULL", (baseline,)).fetchone()[0]
                    if age > 50 or any(a[0] == b[0] for a, b in zip(plays, plays[1:])):
                        raise RuntimeError('Music transition stalled or immediate repeat')
                with (OUT / 'engine.log').open() as reader:
                    reader.seek(log_offset)
                    diagnostics = reader.read().lower()
                    log_offset = reader.tell()
                if any(word in diagnostics for word in ('error', 'failed', 'invalid', 'traceback', 'broken pipe')):
                    raise RuntimeError('Engine/FFmpeg error; see engine.log')
                if rss > 768 * 1024**2 or len(processes) > 12 or cpu > 250 or free < 512 * 1024**2 or storage > 512 * 1024**2:
                    raise RuntimeError('Resource safety threshold exceeded')
                if (OUT / 'engine.log').stat().st_size > 10 * 1024**2:
                    raise RuntimeError('Excessive engine log growth')
                report.update(duration_seconds=round(now-start), samples=report['samples']+1,
                    transitions=max(0,len(plays)-1), distinct_tracks=len({p[0] for p in plays}),
                    peak_rss_bytes=max(rss,report['peak_rss_bytes']),
                    peak_cpu_percent=round(max(cpu,report['peak_cpu_percent']),2),
                    peak_storage_bytes=max(storage,report['peak_storage_bytes']))
                samples.write(json.dumps(dict(elapsed=round(now-start),cpu=round(cpu,2),rss=rss,
                    storage=storage,free=free,processes=[p[0] for p in processes],
                    hls_available=state['available'],transitions=report['transitions']))+'\n')
                samples.flush()
                save()
                time.sleep(5)
        if report['distinct_tracks'] != 3 or report['transitions'] < 300:
            raise RuntimeError('Insufficient catalogue cycling')
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', reason=str(exc))
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if child is not None:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    report.update(status='failed', reason='Forced engine cleanup required')
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    break
                time.sleep(0.3)
            child.wait(timeout=3)
            report['remaining_processes'] = members(child.pid)
            if report['remaining_processes']:
                report.update(status='failed', reason='Residual process detected')
            with database() as conn:
                unclosed = conn.execute('SELECT count(*) FROM private_plays WHERE id>? AND ended_at IS NULL', (baseline,)).fetchone()[0]
            report['unclosed_plays'] = unclosed
            if unclosed:
                report.update(status='failed', reason='Unclosed play history')
        if log:
            log.close()
        report.update(finished_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                      duration_seconds=round(time.monotonic()-start))
        save()
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    if sys.argv[1:] == ['--preflight']:
        preflight()
        print('DEV synthetic-only preflight OK')
    elif not sys.argv[1:]:
        raise SystemExit(run())
    else:
        raise SystemExit('Usage: python -B -m dev_endurance [--preflight]')
