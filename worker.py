#!/usr/bin/env python3
"""Continuous radio: decode each MP3 into fixed PCM, then encode one HLS stream.

The worker owns only files it creates in storage/hls. A single instance is required.
"""
import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from config import HLS_PATH, STORAGE_PATH
from db import get_db

STATE_FILE = os.path.join(HLS_PATH, "now.json")
PLAYLIST = os.path.join(HLS_PATH, "stream.m3u8")
RATE = 44100
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 2  # signed 16-bit little-endian PCM
PCM_CHUNK = RATE * BYTES_PER_FRAME // 5  # 200 ms
SEGMENT_DURATION = 4
HEALTH_TIMEOUT = 20  # seconds without a newly published segment
STARTUP_TIMEOUT = 20
DECODER_TIMEOUT = 60  # Private continuous mode: bounded input wait, distinct from HLS health.
STOP = False


class PipelineError(Exception):
    pass


def get_next_track(last_genre_slug):
    """Temporary simple programming: alternate jingle and non-jingle where possible."""
    db = get_db()
    try:
        target = "!= 'jingle'" if last_genre_slug == "jingle" else "= 'jingle'"
        row = db.execute(f"""
            SELECT t.*, g.slug AS genre_slug FROM tracks t
            JOIN genres g ON g.id = t.genre_id
            WHERE t.status = 'approved' AND g.slug {target}
            ORDER BY RANDOM() LIMIT 1
        """).fetchone()
        if row is None:
            row = db.execute("""
                SELECT t.*, g.slug AS genre_slug FROM tracks t
                JOIN genres g ON g.id = t.genre_id
                WHERE t.status = 'approved' ORDER BY RANDOM() LIMIT 1
            """).fetchone()
        return row
    finally:
        db.close()


def write_state(track=None, started_at=None, duration=0, message=None):
    state = {
        "track_id": track["id"] if track else None,
        "title": track["title"] if track else None,
        "artist": track["artist"] if track else None,
        "genre": track["genre_slug"] if track else None,
        "started_at": started_at,
        "duration": duration or 0,
        "updated_at": time.time(),
    }
    if message:
        state["message"] = message
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def clear_hls_dir():
    Path(HLS_PATH).mkdir(parents=True, exist_ok=True)
    for f in Path(HLS_PATH).iterdir():
        if f.name == ".gitkeep" or not (f.name == "stream.m3u8" or
                                         f.name == "now.json" or
                                         f.name == "now.json.tmp" or
                                         (f.name.startswith("seg_") and f.suffix == ".ts")):
            continue
        if f.is_file():
            f.unlink()


def start_encoder(*, threads=None):
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS),
        "-i", "pipe:0", "-c:a", "aac", "-b:a", "128k",
        "-ar", str(RATE), "-ac", str(CHANNELS),
        "-f", "hls", "-hls_time", str(SEGMENT_DURATION),
        "-hls_start_number_source", "epoch_us",
        "-hls_list_size", "6", "-hls_flags", "delete_segments+omit_endlist+temp_file",
        "-hls_segment_filename", os.path.join(HLS_PATH, "seg_%09d.ts"), PLAYLIST,
    ]
    if threads is not None:
        cmd[cmd.index('-c:a'):cmd.index('-c:a')] = ['-threads', str(threads)]
        cmd[1:1] = ['-filter_threads', str(threads), '-filter_complex_threads', str(threads)]
    # stderr inherited: FFmpeg diagnostics appear in the service logs.
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0)


def stop_process(proc):
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for stream in (proc.stdin, proc.stdout):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


class Engine:
    def __init__(self, *, threads=None, continuous=False):
        self.threads = threads
        self.continuous = continuous
        self.pcm_queue = queue.Queue(maxsize=16)  # <= 3.2 seconds, never unbounded audio.
        self.pump_stop = threading.Event()
        self.pump = None
        self.pump_error = None
        self.encoder = None
        self.decoder = None
        self.segment_stamp = None
        self.last_progress = time.monotonic()
        self.started = self.last_progress

    def start(self):
        self.encoder = start_encoder() if self.threads is None else start_encoder(threads=self.threads)
        self.started = self.last_progress = time.monotonic()
        self.segment_stamp = None
        os.set_blocking(self.encoder.stdin.fileno(), False)
        self.pump_stop.clear()
        self.pump_error = None
        if self.continuous:
            self.pump = threading.Thread(target=self._pump_pcm, name='private-pcm-clock', daemon=True)
            self.pump.start()
        print(f"[worker] encoder PID={self.encoder.pid}", flush=True)

    def stop(self):
        self.pump_stop.set()
        if self.pump is not None:
            self.pump.join(timeout=2)
            self.pump = None
        stop_process(self.decoder)
        self.decoder = None
        stop_process(self.encoder)
        self.encoder = None
        while not self.pcm_queue.empty():
            self.pcm_queue.get_nowait()
            self.pcm_queue.task_done()

    def check(self):
        if self.pump_error is not None:
            raise PipelineError(str(self.pump_error))
        if self.encoder is None or self.encoder.poll() is not None:
            raise PipelineError("encodeur FFmpeg arrêté")
        if self.continuous and self.pump is not None and threading.current_thread() is not self.pump:
            if not self.pump.is_alive():
                raise PipelineError('horloge PCM privée arrêtée')
            return  # Only the pump inspects HLS: no competing clock updates or duplicate disk reads.
        now = time.monotonic()
        try:
            # temp_file makes published segments immutable; inspect the playlist too.
            playlist = Path(PLAYLIST).read_text(encoding="utf-8")
            segments = [s.strip() for s in playlist.splitlines() if s.strip().endswith(".ts")]
            if segments:
                last = Path(HLS_PATH, segments[-1])
                stamp = (segments[-1], last.stat().st_size)
                if stamp[1] > 0 and stamp != self.segment_stamp:
                    self.segment_stamp = stamp
                    self.last_progress = now
        except (OSError, UnicodeError):
            pass
        if now - self.last_progress > (HEALTH_TIMEOUT if self.segment_stamp else STARTUP_TIMEOUT):
            raise PipelineError(f"playlist absente ou segments HLS sans progression "
                                f"({now-self.last_progress:.1f}s; encoder={self.encoder.poll()}; "
                                f"decoder={self.decoder.poll() if self.decoder else 'absent'})")

    def send(self, pcm):
        if self.continuous:
            while pcm and not STOP and not self.pump_stop.is_set():
                self.check()
                try:
                    self.pcm_queue.put(pcm, timeout=0.2)
                    return
                except queue.Full:
                    continue
        else:
            self._send_pcm(pcm)

    def _send_pcm(self, pcm):
        """Feed a bounded PCM buffer without blocking indefinitely on an unresponsive encoder."""
        if not pcm:
            return
        view = memoryview(pcm)
        fd = self.encoder.stdin.fileno()
        while view and not STOP and not self.pump_stop.is_set():
            self.check()
            _, ready, _ = select.select([], [fd], [], 0.5)
            if not ready:
                continue
            try:
                sent = os.write(fd, view)
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError) as exc:
                raise PipelineError("pipe encodeur cassé") from exc
            if sent <= 0:
                raise PipelineError("pipe encodeur fermé")
            view = view[sent:]

    def _pump_pcm(self):
        """Keep one encoder clock alive even while a decoder starts/exits or I/O waits."""
        gap_started = None
        warned = False
        deadline = time.monotonic()
        try:
            while not STOP and not self.pump_stop.is_set():
                start = time.monotonic()
                try:
                    pcm = self.pcm_queue.get_nowait()
                    queued = True
                    if warned:
                        print(f'[worker] private PCM resumed after {start-gap_started:.1f}s silence', flush=True)
                    gap_started, warned = None, False
                except queue.Empty:
                    queued = False
                    pcm = bytes(PCM_CHUNK)
                    if gap_started is None:
                        gap_started = start
                    if start-gap_started > DECODER_TIMEOUT:
                        raise PipelineError(f'source PCM inactive depuis {DECODER_TIMEOUT}s; silence arrêté')
                    if not warned and start-gap_started >= 1:
                        print('[worker] private PCM unavailable; temporary silence (decoder/I/O wait)', flush=True)
                        warned = True
                try:
                    self._send_pcm(pcm)
                finally:
                    if queued:
                        self.pcm_queue.task_done()
                # Absolute audio clock: scheduler overhead must not accumulate per chunk.
                deadline += len(pcm)/(RATE*BYTES_PER_FRAME)
                now = time.monotonic()
                if now-deadline > 0.2:
                    deadline = now  # Never catch up a long stall with an unbounded burst.
                self.pump_stop.wait(max(0,deadline-now))
        except (PipelineError, OSError) as exc:
            self.pump_error = exc

    def silence(self, seconds=1):
        """Pace the raw PCM input: FFmpeg cannot infer wall-clock speed from pipe:0."""
        until = time.monotonic() + seconds
        block = bytes(PCM_CHUNK)
        while not STOP and time.monotonic() < until:
            start = time.monotonic()
            self.send(block)
            delay = start + len(block) / (RATE * BYTES_PER_FRAME) - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def play(self, path):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-re", "-i", path, "-vn", "-sn", "-dn", "-map", "0:a:0",
            "-f", "s16le", "-ac", str(CHANNELS), "-ar", str(RATE), "pipe:1",
        ]
        if self.continuous:
            # The PCM pump is the sole clock; the bounded queue supplies backpressure.
            cmd.remove('-re')
        if self.threads is not None:
            cmd[cmd.index('-i'):cmd.index('-i')] = ['-threads', str(self.threads)]
            cmd[1:1] = ['-filter_threads', str(self.threads), '-filter_complex_threads', str(self.threads)]
        self.decoder = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)
        received = 0
        last_pcm = time.monotonic()
        try:
            fd = self.decoder.stdout.fileno()
            while not STOP:
                self.check()
                readable, _, _ = select.select([fd], [], [], 0.5)
                if not readable:
                    if self.continuous and time.monotonic()-last_pcm > DECODER_TIMEOUT:
                        raise PipelineError(f'decodeur sans PCM depuis {DECODER_TIMEOUT}s; '
                                            f'pid={self.decoder.pid}; exit={self.decoder.poll()}')
                    continue
                chunk = os.read(fd, PCM_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                self.send(chunk)
                last_pcm = time.monotonic()
            if STOP:
                return False
            code = self.decoder.wait(timeout=3)
            if self.continuous:
                # Finish the queued tail before changing the displayed track.
                while self.pcm_queue.unfinished_tasks and not STOP:
                    self.check()
                    self.pump_stop.wait(0.05)
            return code == 0 and received > 0 and not STOP
        finally:
            stop_process(self.decoder)
            self.decoder = None


def start_play(track):
    db = get_db()
    try:
        cur = db.execute("INSERT INTO plays (track_id, started_at) VALUES (?, CURRENT_TIMESTAMP)",
                         (track["id"],))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def end_play(play_id):
    if play_id is None:
        return
    db = get_db()
    try:
        db.execute("UPDATE plays SET ended_at = CURRENT_TIMESTAMP WHERE id = ?", (play_id,))
        db.commit()
    finally:
        db.close()


def run():
    global STOP
    Path(HLS_PATH).mkdir(parents=True, exist_ok=True)
    excluded = set()
    last_genre = None
    engine = Engine()
    while not STOP:
        play_id = None
        try:
            clear_hls_dir()
            write_state(message="flux en démarrage")
            engine.start()
            last_genre = None
            excluded.clear()
            while not STOP:
                track = get_next_track(last_genre)
                if track is None or track["id"] in excluded:
                    # Look through the approved catalogue before giving up this cycle.
                    db = get_db()
                    try:
                        candidates = db.execute("""
                            SELECT t.*, g.slug AS genre_slug FROM tracks t
                            JOIN genres g ON g.id=t.genre_id WHERE t.status='approved'
                            ORDER BY RANDOM()
                        """).fetchall()
                    finally:
                        db.close()
                    track = next((t for t in candidates if t["id"] not in excluded), None)
                if track is None:
                    write_state(message="aucun titre diffusable — silence")
                    engine.silence(1)
                    # Recheck excluded files periodically: do not spin on a bad catalogue.
                    if excluded:
                        engine.silence(4)
                        excluded.clear()
                    continue
                path = os.path.join(STORAGE_PATH, track["file_path"])
                if not os.path.isfile(path):
                    print(f"[worker] fichier absent : track {track['id']}", flush=True)
                    excluded.add(track["id"])
                    continue
                play_id = start_play(track)
                write_state(track, time.time(), track["duration_seconds"] or 0)
                print(f"[worker] ▶ {track['title']} ({track['id']})", flush=True)
                success = engine.play(path)
                end_play(play_id)
                play_id = None
                write_state(message="transition")
                if success:
                    last_genre = track["genre_slug"]
                    excluded.clear()
                else:
                    print(f"[worker] décodage impossible : track {track['id']}", flush=True)
                    excluded.add(track["id"])
        except (PipelineError, OSError, subprocess.SubprocessError) as exc:
            print(f"[worker] pipeline défaillant : {exc}; reprise dans 3 s", flush=True)
        finally:
            end_play(play_id)
            engine.stop()
            write_state(message="flux indisponible")
        if not STOP:
            time.sleep(3)
    write_state(message="flux arrêté")


def handle_sigterm(signum, frame):
    global STOP
    STOP = True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)
    run()
