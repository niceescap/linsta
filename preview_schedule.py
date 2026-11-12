"""Private preview scheduling only; no database or public approval changes."""
import random


class ShuffleCycle:
    """One occurrence per cycle; current/next are committed before playback.

    A future jingle can run between current and advance() without drawing again.
    """
    def __init__(self, tracks, *, rng=None):
        self.tracks = list(tracks)
        if not self.tracks:
            raise ValueError('Empty private catalogue')
        self.rng = rng or random.SystemRandom()
        self.drawn = set()
        self.remaining = []
        self.current = self._draw(None)
        self.next_track = self._draw(self.current) if self.tracks else None

    def _draw(self, previous):
        if not self.remaining:
            self.drawn.clear()
            self.remaining = list(self.tracks)
            self.rng.shuffle(self.remaining)
            if len(self.remaining) > 1 and previous and self.remaining[0]['id'] == previous['id']:
                # Swap with a uniformly selected other position, never redraw forever.
                index = self.rng.randrange(1, len(self.remaining))
                self.remaining[0], self.remaining[index] = self.remaining[index], self.remaining[0]
        # A withdrawn reservation can expose the previous cycle's last item.
        # Keep the remaining cycle intact while preferring a different next item.
        if previous and self.remaining[0]['id'] == previous['id']:
            alternatives = [i for i,item in enumerate(self.remaining) if item['id'] != previous['id']]
            if alternatives:
                index = self.rng.choice(alternatives)
                self.remaining[0],self.remaining[index] = self.remaining[index],self.remaining[0]
        selected = self.remaining.pop(0)
        self.drawn.add(selected['id'])
        return selected

    def refresh(self, tracks):
        """Keep playing current; invalidate only future reservations, no replay in cycle."""
        rows = {item['id']: item for item in tracks}
        self.tracks = list(rows.values())
        self.remaining = [rows[item['id']] for item in self.remaining if item['id'] in rows]
        queued = {item['id'] for item in self.remaining}
        added = [item for tid,item in rows.items() if tid not in self.drawn and tid not in queued]
        self.rng.shuffle(added)
        self.remaining.extend(added)
        if self.current and self.current['id'] in rows:
            self.current = rows[self.current['id']]
        if self.next_track and self.next_track['id'] in rows:
            self.next_track = rows[self.next_track['id']]
        else:
            self.next_track = self._draw(self.current) if rows else None

    def advance(self):
        self.current = self.next_track
        self.next_track = self._draw(self.current) if self.tracks else None
