import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import array
import hashlib
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from media_categorizer.video_export import VideoExport, ExportCancelled, tool, probe, signature, CREATE_FLAGS
from media_categorizer.video_editor import VideoEditor


def ffmpeg(*args):
    return subprocess.run([tool('ffmpeg'), '-hide_banner', '-v', 'error', '-nostdin', '-y', *map(str,args)],
                          capture_output=True, check=True, timeout=60, creationflags=CREATE_FLAGS).stdout


def rms(path):
    samples = array.array('f')
    samples.frombytes(ffmpeg('-i', path, '-map', '0:a:0', '-f', 'f32le', '-ac', '1', '-'))
    return math.sqrt(sum(v*v for v in samples)/len(samples))


class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.input = Path(cls.fixtures.name) / 'source.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24:duration=3',
               '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', cls.input)
        cls.level = rms(cls.input)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'видео.mp4'
        shutil.copyfile(self.input, self.path)
        self.original = self.path.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def test_trim_720p_30fps_and_half_volume(self):
        VideoExport().run(self.path, .5, 2, .5, signature(self.path))
        info = probe(self.path)
        self.assertEqual((info['width'], info['height']), (1280, 720))
        self.assertEqual(info['stream']['avg_frame_rate'], '30/1')
        self.assertAlmostEqual(info['duration'], 1.5, delta=.06)
        self.assertAlmostEqual(rms(self.path)/self.level, .5, delta=.05)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_louder_and_muted_exports(self):
        for gain in (1.5, 0):
            shutil.copyfile(self.input, self.path)
            VideoExport().run(self.path, 0, 1, gain, signature(self.path))
            self.assertAlmostEqual(rms(self.path)/self.level, gain, delta=.08)

    def test_cancel_keeps_original(self):
        job = VideoExport()
        def progress(value):
            job.cancel()
        with self.assertRaises(ExportCancelled):
            job.run(self.path, 0, 3, 1, signature(self.path), progress)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_external_change_and_invalid_trim_keep_original(self):
        expected = signature(self.path)
        self.path.write_bytes(self.original + b'changed')
        with self.assertRaises(ValueError):
            VideoExport().run(self.path, 0, 1, 1, expected)
        current = self.path.read_bytes()
        with self.assertRaises(ValueError):
            VideoExport().run(self.path, 2, 1, 1, signature(self.path))
        self.assertEqual(self.path.read_bytes(), current)

    def test_failed_publish_keeps_original_and_cleans_staging(self):
        with patch('media_categorizer.video_export.os.replace', side_effect=PermissionError('Locked')):
            with self.assertRaises(PermissionError):
                VideoExport().run(self.path, 0, .5, 1, signature(self.path))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_portrait_without_audio(self):
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=120x200:rate=15:duration=1', '-c:v', 'libx264', self.path)
        VideoExport().run(self.path, 0, 1, 1, signature(self.path))
        info = probe(self.path)
        self.assertEqual((info['width'], info['height']), (720, 1280))
        self.assertFalse(info['audio'])

    def test_output_containers_match_original_extension(self):
        for suffix in ('.mov', '.mkv', '.avi', '.webm', '.wmv', '.mpg'):
            with self.subTest(suffix=suffix):
                target = self.path.with_suffix(suffix)
                shutil.copyfile(self.input, target)
                VideoExport().run(target, 0, .5, 1, signature(target))
                info = probe(target)
                self.assertEqual((info['stream']['width'], info['stream']['height']), (1280, 720))
                self.assertTrue(info['audio'])

    def test_dialog_exports_in_background(self):
        editor = VideoEditor(self.path)
        editor.start.setValue(.5)
        editor.end.setValue(1.5)
        editor.volume.setValue(75)
        warning = patch("media_categorizer.video_editor.QMessageBox.warning")
        messages = warning.start()
        self.addCleanup(warning.stop)
        editor.save()
        self.assertFalse(editor.save_btn.isEnabled())
        deadline = time.monotonic() + 30
        while editor.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(editor.worker)
        self.assertFalse(messages.called, str(messages.call_args))
        self.assertEqual(editor.result(), VideoEditor.Accepted)
        self.assertAlmostEqual(probe(self.path)['duration'], 1, delta=.1)
        editor.deleteLater()
