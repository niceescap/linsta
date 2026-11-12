"""Local MP3 station IDs, separate from the private track catalogue and shuffle."""
from pathlib import Path
import random
from mutagen import MutagenError


def directory(root):
    return Path(root) / 'jingles' / 'JINGLLE'


def scan(root):
    from private_radio import validate_file
    folder = directory(root)
    if any(p.is_symlink() for p in (folder, *folder.parents)) or not folder.is_dir():
        return []
    result = []
    try:
        paths = sorted(folder.iterdir())
    except OSError:
        return []
    for path in paths:
        if not path.name.lower().endswith('.mp3'):
            continue
        item = dict(name=path.name, duration=None, valid=False)
        try:
            digest, size, duration = validate_file(folder, path.name)
            item.update(valid=True, duration=duration, sha256=digest, size=size)
        except (OSError, ValueError, MutagenError) as error:
            item['error'] = type(error).__name__
        result.append(item)
    return result


class JinglePicker:
    def __init__(self, items, *, rng=None):
        self.items = [item for item in items if item['valid']]
        self.rng = rng or random.SystemRandom()
        self.previous = None

    def choose(self):
        candidates = [item for item in self.items if item['name'] != self.previous]
        if not candidates:
            candidates = self.items
        if not candidates:
            return None
        selected = self.rng.choice(candidates)
        self.previous = selected['name']
        return selected
