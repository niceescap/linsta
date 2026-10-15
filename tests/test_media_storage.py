"""Real Mutagen/Pillow checks on synthetic bytes, never FFmpeg or network."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from audio_storage import (MediaLimits, StorageError, StorageSession, checked_open,
                           prepare_audio)
from cover_storage import prepare_cover


def mp3_bytes():
    # MPEG-1 Layer III 128 kb/s, 44.1 kHz frames for structural parser testing.
    return (b'\xff\xfb\x90\x00'+b'\0'*413)*100


def cover_bytes(fmt='PNG',size=(8,6)):
    stream=io.BytesIO()
    image=Image.new('RGB',size,color='blue')
    metadata=PngImagePlugin.PngInfo()
    metadata.add_text('Comment','private metadata')
    image.save(stream,format=fmt,**({'pnginfo':metadata} if fmt=='PNG' else {}))
    image.close()
    return stream.getvalue()


class MediaStorageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.session=StorageSession(self.root,self.root/'meta')
        self.addCleanup(self.session.cleanup)
        self.limits=MediaLimits()
        network=patch('socket.socket',side_effect=AssertionError('No network'))
        network.start()
        self.addCleanup(network.stop)

    def test_mutagen_checks_real_frame_structure_and_hash(self):
        raw=mp3_bytes()
        audio=prepare_audio(self.session,io.BytesIO(raw),'test.mp3',self.limits)
        self.assertEqual(audio.sha256,hashlib.sha256(raw).hexdigest())
        self.assertEqual(audio.size,len(raw))
        self.assertGreater(audio.duration,2)
        self.assertEqual(audio.mime,'audio/mpeg')

    def test_empty_corrupt_and_false_mp3_rejected(self):
        for raw in (b'',b'ID3-only',cover_bytes(),b'not an mp3'):
            with self.subTest(raw=raw[:10]), self.assertRaises(StorageError):
                prepare_audio(self.session,io.BytesIO(raw),'fake.mp3',self.limits)

    def test_audio_extension_size_and_duration_limits(self):
        for filename,limits in (('test.wav',self.limits),('test.mp3',replace(self.limits,audio_bytes=10)),
                                ('test.mp3',replace(self.limits,duration_seconds=1))):
            with self.assertRaises(StorageError): prepare_audio(self.session,io.BytesIO(mp3_bytes()),filename,limits)

    def test_non_finite_or_suspicious_mp3_info_rejected(self):
        from types import SimpleNamespace
        for changes in ({'length':float('nan')},{'length':float('inf')},{'sketchy':True},{'layer':2}):
            values=dict(length=10,layer=3,sketchy=False,channels=2,sample_rate=44100,bitrate=128000)
            values.update(changes)
            with patch('audio_storage.MP3',return_value=SimpleNamespace(info=SimpleNamespace(**values))):
                with self.assertRaises(StorageError): prepare_audio(self.session,io.BytesIO(mp3_bytes()),'test.mp3',self.limits)

    def test_both_image_formats_fully_decode_and_normalize(self):
        for fmt,extension in (('JPEG','jpg'),('PNG','png')):
            original,display=prepare_cover(self.session,io.BytesIO(cover_bytes(fmt)),f'cover.{extension}',self.limits)
            self.assertEqual((original.width,original.height),(8,6))
            self.assertNotEqual(original.path,display.path)
            with Image.open(display.path) as image:
                image.load()
                self.assertEqual(image.format,fmt)
                self.assertNotIn('Comment',image.info)
            self.assertEqual(display.sha256,hashlib.sha256(display.path.read_bytes()).hexdigest())

    def test_image_extension_must_match_content(self):
        with self.assertRaises(StorageError): prepare_cover(self.session,io.BytesIO(cover_bytes('PNG')),'cover.jpg',self.limits)
        with self.assertRaises(StorageError): prepare_cover(self.session,io.BytesIO(cover_bytes('BMP')),'cover.png',self.limits)

    def test_corrupted_and_svg_covers_rejected(self):
        for raw,name in ((b'<svg/>','cover.svg'),(b'invalid','cover.png'),(cover_bytes()[:30],'cover.png')):
            with self.assertRaises(StorageError): prepare_cover(self.session,io.BytesIO(raw),name,self.limits)

    def test_image_pixel_dimension_and_byte_limits(self):
        for limits in (replace(self.limits,cover_pixels=10),replace(self.limits,cover_dimension=4),
                       replace(self.limits,cover_bytes=10)):
            with self.assertRaises(StorageError): prepare_cover(self.session,io.BytesIO(cover_bytes()),'cover.png',limits)

    def test_animated_png_rejected(self):
        raw=io.BytesIO()
        first=Image.new('RGB',(8,8),'red')
        second=Image.new('RGB',(8,8),'green')
        first.save(raw,format='PNG',save_all=True,append_images=[second],duration=100,loop=0)
        first.close(); second.close()
        with self.assertRaises(StorageError): prepare_cover(self.session,io.BytesIO(raw.getvalue()),'cover.png',self.limits)

    def test_publish_never_overwrites_existing_file(self):
        audio=prepare_audio(self.session,io.BytesIO(mp3_bytes()),'test.mp3',self.limits)
        existing=self.session.tracks/'digger-collision.mp3'
        existing.write_bytes(b'existing')
        with self.assertRaises(FileExistsError): self.session.publish(audio,self.session.tracks,existing.name)
        self.session.cleanup()
        self.assertEqual(existing.read_bytes(),b'existing')

    def test_staging_mutation_prevents_publication(self):
        audio=prepare_audio(self.session,io.BytesIO(mp3_bytes()),'test.mp3',self.limits)
        audio.path.write_bytes(b'replacement')
        with self.assertRaises(StorageError): self.session.publish(audio,self.session.tracks,'digger-new.mp3')

    def test_cleanup_does_not_delete_substituted_or_historical_files(self):
        prepared=self.session.receive(io.BytesIO(b'owned'),'file',100)
        prepared.path.rename(self.root/'moved-original')
        prepared.path.write_bytes(b'substituted')
        historical=self.session.tracks/'historical.mp3'
        historical.write_bytes(b'historical')
        with self.assertLogs('audio_storage',level='WARNING') as logs:
            self.assertEqual(self.session.cleanup(),[prepared.path.name])
        self.assertEqual(logs.output,['WARNING:audio_storage:Media cleanup deferred for 1 owned files'])
        self.assertEqual(prepared.path.read_bytes(),b'substituted')
        self.assertEqual(historical.read_bytes(),b'historical')

    def test_verified_read_rejects_symlinks_and_wrong_hash(self):
        path=self.session.tracks/'file.mp3'
        path.write_bytes(b'audio')
        link=self.session.tracks/'link.mp3'
        link.symlink_to(path)
        for name,digest in (('link.mp3',None),('file.mp3','0'*64),('../file.mp3',None)):
            with self.assertRaises(StorageError): checked_open(self.session.tracks,name,sha256=digest)

    def test_root_symlink_rejected(self):
        link=self.root/'alias'
        link.symlink_to(self.session.meta,target_is_directory=True)
        with self.assertRaises(StorageError): StorageSession(self.root,link)

    def test_disk_failure_cleanup_is_limited_to_owned_files(self):
        historical=self.session.tracks/'legacy.mp3'
        historical.write_bytes(b'legacy')
        with patch('audio_storage.os.fsync',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError): self.session.receive(io.BytesIO(b'test'),'test',100)
        self.session.cleanup()
        self.assertEqual(list(self.session.staging.iterdir()),[])
        self.assertTrue(historical.exists())

    def test_cleanup_permission_failure_is_reported_and_can_be_retried(self):
        prepared=self.session.receive(io.BytesIO(b'owned'),'file',100)
        historical=self.session.tracks/'historical.mp3'
        historical.write_bytes(b'historical')
        with patch.object(Path,'unlink',side_effect=PermissionError('simulated cleanup denial')):
            with self.assertLogs('audio_storage',level='WARNING') as logs:
                self.assertEqual(self.session.cleanup(),[prepared.path.name])
        self.assertEqual(logs.output,['WARNING:audio_storage:Media cleanup deferred for 1 owned files'])
        self.assertEqual(prepared.path.read_bytes(),b'owned')
        self.assertEqual(self.session.cleanup(),[])
        self.assertFalse(prepared.path.exists())
        self.assertEqual(historical.read_bytes(),b'historical')

    def test_published_files_survive_success_cleanup_and_are_read_only(self):
        prepared=self.session.receive(io.BytesIO(b'test'),'test',100)
        final=self.session.publish(prepared,self.session.tracks,'digger-file.mp3')
        self.session.cleanup(keep_published=True)
        self.assertTrue(final.is_file())
        self.assertEqual(final.stat().st_mode & 0o222,0)
        self.assertEqual(list(self.session.staging.iterdir()),[])

    def test_configurable_positive_limits(self):
        self.assertEqual(MediaLimits.from_environment({'DIGGER_AUDIO_BYTES':'1234'}).audio_bytes,1234)
        for value in ('0','-1','bad'):
            with self.assertRaises(ValueError): MediaLimits.from_environment({'DIGGER_AUDIO_BYTES':value})
