#!/usr/bin/env python3
"""Programmateur radio : alterne jingle <-> (funk|deep house) et alimente un flux HLS continu."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from config import STORAGE_PATH, HLS_PATH
from db import get_db

STATE_FILE = os.path.join(HLS_PATH, "now.json")
SEGMENT_DURATION = 4


def get_next_track(last_genre_slug):
    """Alterne jingle <-> non-jingle. Fallback : n'importe quel track approuvé."""
    db = get_db()
    if last_genre_slug == "jingle":
        # On vient de jouer un jingle → on veut un non-jingle
        row = db.execute("""
            SELECT t.*, g.slug AS genre_slug
            FROM tracks t JOIN genres g ON g.id = t.genre_id
            WHERE t.status='approved' AND g.slug != 'jingle'
            ORDER BY RANDOM() LIMIT 1
        """).fetchone()
    else:
        # On vient de jouer un track (ou c'est le démarrage) → on veut un jingle
        row = db.execute("""
            SELECT t.*, g.slug AS genre_slug
            FROM tracks t JOIN genres g ON g.id = t.genre_id
            WHERE t.status='approved' AND g.slug = 'jingle'
            ORDER BY RANDOM() LIMIT 1
        """).fetchone()

    if not row:
        # Fallback : n'importe quoi
        row = db.execute("""
            SELECT t.*, g.slug AS genre_slug
            FROM tracks t JOIN genres g ON g.id = t.genre_id
            WHERE t.status='approved'
            ORDER BY RANDOM() LIMIT 1
        """).fetchone()
    db.close()
    return row


def write_state(track, started_at, duration):
    state = {
        "track_id": track["id"] if track else None,
        "title":    track["title"] if track else None,
        "artist":   track["artist"] if track else None,
        "genre":    track["genre_slug"] if track else None,
        "started_at": started_at,
        "duration":   duration or 0,
        "updated_at": time.time(),
    }
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def clear_hls_dir():
    for f in Path(HLS_PATH).iterdir():
        if f.name == ".gitkeep":
            continue
        try:
            f.unlink()
        except (IsADirectoryError, FileNotFoundError):
            pass


def start_ffmpeg():
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "warning",
        "-re",
        "-f", "mp3",
        "-i", "pipe:0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-f", "hls",
        "-hls_time", str(SEGMENT_DURATION),
        "-hls_list_size", "6",
        "-hls_flags", "delete_segments+omit_endlist",
        "-hls_segment_filename", os.path.join(HLS_PATH, "seg_%06d.ts"),
        os.path.join(HLS_PATH, "stream.m3u8"),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def run():
    clear_hls_dir()
    ffmpeg = start_ffmpeg()
    print(f"[worker] ffmpeg PID={ffmpeg.pid}, HLS → {HLS_PATH}", flush=True)

    last_genre = None
    idle_logged = False

    while True:
        # ffmpeg crashé ? on le relance et on redémarre la boucle propre
        if ffmpeg.poll() is not None:
            print("[worker] ffmpeg est mort, redémarrage dans 3 s", flush=True)
            time.sleep(3)
            try:
                ffmpeg.stdin.close()
            except Exception:
                pass
            ffmpeg = start_ffmpeg()
            last_genre = None

        track = get_next_track(last_genre)

        if not track:
            if not idle_logged:
                print("[worker] aucun track approuvé — pause 5 s en boucle", flush=True)
                idle_logged = True
            write_state(None, time.time(), 0)
            time.sleep(5)
            continue
        idle_logged = False

        abs_path = os.path.join(STORAGE_PATH, track["file_path"])
        if not os.path.exists(abs_path):
            print(f"[worker] fichier manquant : {abs_path} — skip", flush=True)
            last_genre = track["genre_slug"]
            continue

        duration = track["duration_seconds"] or 0
        started_at = time.time()
        write_state(track, started_at, duration)

        db = get_db()
        cur = db.execute(
            "INSERT INTO plays (track_id, started_at) VALUES (?, CURRENT_TIMESTAMP)",
            (track["id"],),
        )
        play_id = cur.lastrowid
        db.commit()
        db.close()

        print(
            f"[worker] ▶ {track['title']} — {track['artist'] or '?'} "
            f"[{track['genre_slug']}] ({duration:.0f} s)",
            flush=True,
        )

        try:
            with open(abs_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    ffmpeg.stdin.write(chunk)
                    ffmpeg.stdin.flush()
        except BrokenPipeError:
            print("[worker] pipe ffmpeg cassé — on relance ffmpeg", flush=True)
            last_genre = track["genre_slug"]
            continue

        # Fin du track : on clôt le play, on garde le même ffmpeg vivant
        db = get_db()
        db.execute("UPDATE plays SET ended_at = CURRENT_TIMESTAMP WHERE id = ?", (play_id,))
        db.commit()
        db.close()

        last_genre = track["genre_slug"]


def handle_sigterm(signum, frame):
    print("[worker] arrêt demandé", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        run()
    except KeyboardInterrupt:
        pass
