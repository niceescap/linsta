import random
import unittest
from preview_schedule import ShuffleCycle


class ShuffleTests(unittest.TestCase):
    def test_complete_cycles_and_committed_next(self):
        for size in (1, 2, 3, 26):
            for seed in range(12):
                with self.subTest(size=size, seed=seed):
                    schedule = ShuffleCycle([{'id': i} for i in range(size)], rng=random.Random(seed))
                    played = []
                    for _ in range(size * 8):
                        played.append(schedule.current['id'])
                        selected = schedule.next_track
                        # A future jingle or status read does not consume another draw.
                        self.assertIs(schedule.next_track, selected)
                        schedule.advance()
                        self.assertIs(schedule.current, selected)
                    for offset in range(0, len(played), size):
                        self.assertEqual(set(played[offset:offset+size]), set(range(size)))
                    if size > 1:
                        self.assertTrue(all(a != b for a, b in zip(played, played[1:])))

    def test_order_is_randomized_and_new_cycles_are_shuffled(self):
        schedule = ShuffleCycle([{'id': i} for i in range(26)], rng=random.Random(17))
        cycles = []
        for _ in range(3):
            cycle = []
            for _ in range(26):
                cycle.append(schedule.current['id'])
                schedule.advance()
            cycles.append(cycle)
        self.assertNotEqual(cycles[0], list(range(26)))
        self.assertNotEqual(cycles[0], cycles[1])
        self.assertNotEqual(cycles[1], cycles[2])

    def test_empty_catalogue_is_rejected(self):
        with self.assertRaises(ValueError):
            ShuffleCycle([])
