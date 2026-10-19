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
        self.remaining = []
        self.current = self._draw(None)
        self.next_track = self._draw(self.current)

    def _draw(self, previous):
        if not self.remaining:
            self.remaining = list(self.tracks)
            self.rng.shuffle(self.remaining)
            if len(self.remaining) > 1 and previous and self.remaining[0]['id'] == previous['id']:
                # Swap with a uniformly selected other position, never redraw forever.
                index = self.rng.randrange(1, len(self.remaining))
                self.remaining[0], self.remaining[index] = self.remaining[index], self.remaining[0]
        return self.remaining.pop(0)

    def advance(self):
        self.current = self.next_track
        self.next_track = self._draw(self.current)
