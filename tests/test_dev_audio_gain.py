"""DEV gain is bound to measured source bytes, with headroom and no source edits."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from dev_audio_gain import MeasuredGain


class GainTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.audio=self.root/'song.mp3'
        self.audio.write_bytes(b'private test fixture')
        self.report=self.root/'levels.json'
        self.report.write_text(json.dumps({'items':[{'name':'song.mp3','sha256':hashlib.sha256(self.audio.read_bytes()).hexdigest(),'integrated_lufs':-10,'peak_dbfs':-.1}]}))

    def test_cached_measurement_attenuates_without_changing_source(self):
        before=self.audio.read_bytes()
        gain=MeasuredGain(self.root,self.report)
        with patch('dev_audio_gain.subprocess.run',side_effect=AssertionError('No unnecessary analysis')):
            self.assertEqual(gain(self.audio),-8)
            self.assertEqual(gain(self.audio),-8)
        self.assertEqual(self.audio.read_bytes(),before)

    def test_changed_bytes_require_new_measurement_and_peak_headroom(self):
        gain=MeasuredGain(self.root,self.report)
        self.audio.write_bytes(b'new private fixture bytes')
        summary='Summary:\n I: -24.0 LUFS\n Peak: -2.0 dBFS\n'
        with patch('dev_audio_gain.subprocess.run',return_value=SimpleNamespace(stderr=summary)) as run:
            self.assertEqual(gain(self.audio),.5)
            run.assert_called_once()
        self.assertEqual(self.audio.read_bytes(),b'new private fixture bytes')

    def test_symlink_and_outside_source_rejected(self):
        gain=MeasuredGain(self.root,self.report)
        link=self.root/'link.mp3'
        link.symlink_to(self.audio)
        with self.assertRaises(ValueError):gain(link)
        with self.assertRaises(ValueError):gain(Path('/tmp/outside.mp3'))
