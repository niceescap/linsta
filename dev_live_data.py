"""DEV metadata resolved against emitted PCM timing and published HLS, never file order."""
from datetime import datetime
import json
import math
import sqlite3

from flask import url_for
import preview_editorial
from preview_radio_web import read_small, track, PreviewError


def published_window(playlist):
    stamp = None
    duration = None
    starts = []
    ends = []
    for line in playlist.decode('utf-8').splitlines():
        if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            stamp = datetime.fromisoformat(line.split(':',1)[1].replace('Z','+00:00')).timestamp()
        elif line.startswith('#EXTINF:'):
            duration = float(line.split(':',1)[1].split(',')[0])
        elif line and not line.startswith('#'):
            if stamp is None or duration is None or not math.isfinite(stamp) or not 0 < duration <= 30:
                raise ValueError('Unverifiable HLS timing')
            starts.append(stamp)
            ends.append(stamp+duration)
            stamp += duration
            duration = None
    if not ends:
        raise ValueError('No published media')
    return min(starts), max(ends)


def enrich(root, state, result, requested_at):
    result.update(history=[], history_available=False, metadata_basis='unavailable',
                  metadata_status='waiting', playback_ended_at=None,
                  next_metadata_basis='unavailable')
    if not state['available']:
        return result
    # Clear encoder-ahead metadata until the published HLS boundary proves its presence.
    result.update(track_id=None,type=None,title='Synchronisation du direct…',artist=None,
                  cover_url=None,timing_available=False,elapsed=None,duration=None,
                  next_track_id=None,next_title=None,next_artist=None,next_cover_url=None)
    try:
        window_start, edge = published_window(state['playlist'])
        timeline = json.loads(read_small(root/'hls','timeline.json',65536)[0])
        origin = timeline['origin']
        if type(origin) not in (int,float) or not math.isfinite(origin) or timeline['started_at'] < state['started_at']:
            raise ValueError('Old/unanchored timeline')
        entries = timeline['tracks']
        previous = -1
        for item in entries:
            offset = item['offset']
            if type(offset) not in (int,float) or not math.isfinite(offset) or offset < 0 or offset <= previous:
                raise ValueError('Invalid timeline ordering')
            previous = offset
        def reserve(item, basis):
            # Reservation is independent of the browser's audible-clock API.
            result.update(next_track_id=None,next_title=None,next_artist=None,next_cover_url=None,
                          next_metadata_basis='unavailable')
            following = item.get('next_track_id')
            if type(following) is int:
                row = track(root,following)
                if preview_editorial.eligible(row):
                    result.update(next_track_id=row['id'],next_title=row['title'],next_artist=row['artist'],
                                  next_cover_url=url_for('preview_radio.cover',track_id=row['id']),
                                  next_metadata_basis=basis)

        published = [item for item in entries if origin+item['offset'] <= edge-.001]
        if published:
            reserve(published[-1], 'published-live-edge')
        # The published playlist is a sliding download window, NOT the listener's
        # buffered playback window. Native Safari can still play older segments.
        # Resolve those positions against the retained emitted-PCM timeline.
        # Never accept a future position or one preceding this retained session.
        at = edge - .001 if requested_at is None else float(requested_at)
        result.update(published_start=window_start,published_end=edge)
        if not math.isfinite(at) or not entries or not origin+entries[0]['offset'] <= at <= edge+.1:
            result['metadata_status']='clock-outside-emitted-timeline'
            raise ValueError('Position outside emitted/published media')
        at = min(at,edge-.001)
        audible = [item for item in entries if origin+item['offset'] <= at]
        if not audible:
            raise ValueError('No emitted item at playback time')

        def describe(item):
            if item.get('type') == 'jingle' and item.get('id') is None:
                return dict(type='jingle',track_id=None,title='Jingle Digger Radio',artist=None,
                            cover_url=None,duration=item.get('duration'))
            if type(item.get('id')) is not int:
                raise ValueError('Unknown item')
            row = track(root,item['id'])
            return dict(type='track',track_id=row['id'],title=row['title'],artist=row['artist'],
                        cover_url=url_for('preview_radio.cover',track_id=row['id']),duration=row['duration'],
                        genre=row.get('genre') or None,year=row.get('year'))

        current = audible[-1]
        # The following emitted entry provides the exact output boundary (source
        # duration alone differs slightly from the PCM block boundaries).
        following_entry = next((item for item in entries if item['offset'] > current['offset']),None)
        result.update(describe(current),elapsed=max(0,at-origin-current['offset']),
                      playback_started_at=origin+current['offset'],timing_available=requested_at is not None,
                      playback_ended_at=origin+following_entry['offset'] if following_entry else None,
                      metadata_at=at,metadata_status='synchronized' if requested_at is not None else 'awaiting-playback-clock',
                      metadata_basis='audible-clock' if requested_at is not None else 'published-live-edge')
        reserve(current, 'audible-clock' if requested_at is not None else 'published-live-edge')
        history = []
        for item, following_item in zip(entries,entries[1:]):
            end = origin + following_item['offset']
            if end > at:
                break
            try:
                history.append(dict(describe(item),started_at=origin+item['offset'],ended_at=end))
            except (PreviewError,ValueError,sqlite3.Error):
                continue
        result.update(history=history[-8:][::-1],history_available=True)
    except (OSError,ValueError,TypeError,KeyError,PreviewError,sqlite3.Error):
        # The flux may remain healthy while timing/metadata is temporarily unavailable.
        result.update(history=[],history_available=False)
    return result
