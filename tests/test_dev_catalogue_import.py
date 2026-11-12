"""Copy authorized snapshots into a temporary DEV root, never alter source rows/media."""
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import dev_import_private_catalogue as importer
import private_radio
import private_radio_admin
import preview_editorial
from test_private_radio import PrivateRadioTests


class ImportTests(unittest.TestCase):
    setUpClass = classmethod(PrivateRadioTests.setUpClass.__func__)
    setUp = PrivateRadioTests.setUp
    imported = PrivateRadioTests.imported

    def test_snapshot_preserves_source_rights_and_is_idempotent(self):
        self.imported()
        source=self.root
        private_radio_admin.migrate(source)
        preview_editorial.migrate(source)
        with sqlite3.connect(source/'catalogue.sqlite') as c:
            before=list(c.iterdump())
        destination=source.parent/'dev'/'private-radio'
        private_radio.initialize(destination)
        private_radio_admin.migrate(destination)
        preview_editorial.migrate(destination)
        patches=[patch.object(importer,'SOURCE',source),patch.object(importer,'DESTINATION',destination),
                 patch.object(importer.dev_runtime,'ROOT',destination.parent),patch.object(importer.dev_runtime,'check_paths')]
        for p in patches:p.start();self.addCleanup(p.stop)
        importer.run()
        with sqlite3.connect(destination/'catalogue.sqlite') as c:
            rows=c.execute('SELECT sha256,private_rights_confirmed,validated_at FROM private_tracks').fetchall()
        # The two input fixture filenames have identical bytes: one unique source is admitted.
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][1],1)
        importer.run()
        with sqlite3.connect(destination/'catalogue.sqlite') as c:
            self.assertEqual(c.execute('SELECT count(*) FROM private_tracks').fetchone()[0],1)
        with sqlite3.connect(source/'catalogue.sqlite') as c:
            self.assertEqual(list(c.iterdump()),before)
        self.assertEqual((source/'one.mp3').read_bytes(),self.audio)
        self.assertEqual((destination/'one.mp3').stat().st_mode & 0o777,0o600)

    def test_conflicting_existing_media_is_not_overwritten(self):
        target=self.root/'copy'
        target.mkdir()
        (target/'one.mp3').write_bytes(b'different existing bytes')
        with self.assertRaises(ValueError):
            importer.copy_verified(self.root,'one.mp3',target,digest=hashlib.sha256(self.audio).hexdigest(),size=len(self.audio))
        self.assertEqual((target/'one.mp3').read_bytes(),b'different existing bytes')
