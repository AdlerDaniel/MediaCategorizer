import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from media_categorizer.video_audio import AudioWaveform, RemoveAudio
from media_categorizer.audio_widgets import WaveformWidget
from media_categorizer.video_export import CREATE_FLAGS, ExportCancelled, probe, signature, tool
from media_categorizer_v4_5 import MediaCategorizer


def ffmpeg(*args):
    return subprocess.run([tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', *map(str, args)],
                          check=True, capture_output=True, timeout=60, creationflags=CREATE_FLAGS).stdout


class VideoAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.input = Path(cls.fixtures.name) / 'source.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=24:duration=3',
               '-f', 'lavfi', '-i', r'aevalsrc=if(lt(t\,1)\,0\,if(lt(t\,2)\,0.15\,0.7))*sin(2*PI*440*t):s=16000:d=3',
               '-c:v', 'libx264', '-c:a', 'aac', cls.input)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'видео.mp4'
        shutil.copyfile(self.input, self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_waveform_preserves_quiet_and_loud_intervals(self):
        peaks, duration = AudioWaveform().run(self.path, signature(self.path), 300)
        self.assertAlmostEqual(duration, 3, delta=.05)
        self.assertLess(max(peaks[:90]), .01)
        self.assertGreater(max(peaks[110:190]), .1)
        self.assertGreater(max(peaks[210:290]), .5)
        self.assertLess(max(peaks[110:190]), max(peaks[210:290]) / 2)
        self.assertEqual(len(peaks), 300)

    def test_waveform_delayed_antiphase_stereo_does_not_disappear(self):
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=24:duration=3',
               '-itsoffset', '1', '-f', 'lavfi', '-i',
               'aevalsrc=0.5*sin(2*PI*440*t)|-0.5*sin(2*PI*440*t):s=16000:d=2',
               '-c:v', 'libx264', '-c:a', 'aac', self.path)
        peaks, _ = AudioWaveform().run(self.path, signature(self.path), 300)
        self.assertLess(max(peaks[:85]), .01)
        self.assertGreater(max(peaks[120:190]), .4)

    def test_real_progressive_prefix_matches_final_data(self):
        updates = []
        final, duration = AudioWaveform().run(self.path, signature(self.path), 300,
                                              partial=lambda *args: updates.append(args))
        self.assertTrue(updates)
        previous = 0
        for peaks, seconds, bins in updates:
            self.assertGreater(len(peaks), previous)
            self.assertEqual(peaks, final[:len(peaks)])
            self.assertEqual(bins, len(final))
            self.assertEqual(seconds, duration)
            previous = len(peaks)

    def test_quiet_high_frequency_audio_is_preserved_and_visible(self):
        target = self.folder / 'quiet.mkv'
        ffmpeg('-f', 'lavfi', '-i', 'color=size=160x120:duration=3',
               '-f', 'lavfi', '-i', 'aevalsrc=0.005*sin(2*PI*12000*t):s=48000:d=3',
               '-c:v', 'libx264', '-c:a', 'pcm_f32le', target)
        peaks, duration = AudioWaveform().run(target, signature(target), 300)
        self.assertGreater(min(peaks[10:290]), .004)
        widget = WaveformWidget()
        widget.resize(600, 62)
        widget.set_data(peaks, duration)
        self.assertGreater(widget.amplitude(max(peaks)), .3)
        image = widget.grab().toImage()
        # Audible quiet samples must extend well above the center line.
        self.assertNotEqual(image.pixelColor(300, 22), image.pixelColor(300, 8))
        self.assertEqual(widget.amplitude(0), 0)
        widget.deleteLater()

    def test_partial_wave_does_not_stretch_or_draw_unknown_audio(self):
        widget = WaveformWidget()
        widget.resize(618, 62)
        widget.set_loading()
        self.assertEqual(widget.message, '')
        widget.set_partial([.5] * 50, 30, 200)
        widget.visible_bins = 50
        partial = widget.grab().toImage()
        self.assertNotEqual(partial.pixelColor(40, 20), partial.pixelColor(400, 20))
        widget.set_data([.5] * 200, 30, animate=True)
        final = widget.grab().toImage()
        self.assertEqual(partial.pixelColor(40, 20), final.pixelColor(40, 20))
        self.assertEqual(widget.visible_bins, 50)
        widget._seek(309)
        self.assertAlmostEqual(widget.position, 15, delta=.1)
        for _ in range(30):
            widget._reveal()
        self.assertEqual(widget.visible_bins, 200)
        widget.set_loading()
        self.assertFalse(widget.reveal_timer.isActive())
        self.assertFalse(widget.peaks)
        widget.deleteLater()

    def test_remove_all_audio_preserves_decoded_video_and_parameters(self):
        ffmpeg('-i', self.input, '-f', 'lavfi', '-i', 'sine=frequency=880:duration=3',
               '-map', '0:v', '-map', '0:a', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', self.path)
        before = probe(self.path)
        digest = ffmpeg('-i', self.path, '-map', '0:v:0', '-f', 'framemd5', '-')
        progress = []
        RemoveAudio().run(self.path, signature(self.path), progress.append)
        after = probe(self.path)
        self.assertFalse(after['audio'])
        self.assertEqual(before['stream']['avg_frame_rate'], after['stream']['avg_frame_rate'])
        self.assertEqual(digest, ffmpeg('-i', self.path, '-map', '0:v:0', '-f', 'framemd5', '-'))
        self.assertEqual(progress[-1], 100)
        self.assertEqual(list(self.folder.iterdir()), [self.path])
        self.assertEqual(AudioWaveform().run(self.path, signature(self.path))[0], ())

    def test_rotation_and_containers_are_preserved(self):
        for suffix in ('.mp4', '.mov', '.mkv', '.avi', '.webm', '.wmv', '.mpg'):
            target = self.path.with_suffix(suffix)
            args = ['-i', self.input]
            if suffix == '.webm': args += ['-c:v', 'libvpx-vp9', '-c:a', 'libopus']
            elif suffix == '.wmv': args += ['-c:v', 'wmv2', '-c:a', 'wmav2']
            elif suffix == '.mpg': args += ['-c:v', 'mpeg2video', '-c:a', 'mp2']
            else: args += ['-c', 'copy']
            if suffix in ('.mp4', '.mov'): args += ['-metadata:s:v:0', 'rotate=90']
            ffmpeg(*args, target)
            before = probe(target)
            RemoveAudio().run(target, signature(target))
            after = probe(target)
            self.assertEqual((before['width'], before['height']), (after['width'], after['height']), suffix)
            self.assertFalse(after['audio'])

    def test_failed_publish_cancel_and_external_change_keep_original(self):
        original = self.path.read_bytes()
        with patch('media_categorizer.video_audio.os.replace', side_effect=PermissionError('Locked')):
            with self.assertRaises(PermissionError):
                RemoveAudio().run(self.path, signature(self.path))
        self.assertEqual(self.path.read_bytes(), original)
        job = RemoveAudio()
        with self.assertRaises(ExportCancelled):
            job.run(self.path, signature(self.path), lambda v: job.cancel())
        self.assertEqual(self.path.read_bytes(), original)
        expected = signature(self.path)
        self.path.write_bytes(original + b'changed')
        with self.assertRaises(ValueError):
            RemoveAudio().run(self.path, expected)
        self.assertEqual(self.path.read_bytes(), original + b'changed')
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_click_waveform_seeks_to_same_time(self):
        widget = WaveformWidget()
        widget.resize(600, 62)
        widget.set_data([.3] * 300, 30)
        values = []
        widget.seekRequested.connect(values.append)
        widget.show()
        self.app.processEvents()
        QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(300, 30))
        self.assertAlmostEqual(values[-1], 15, delta=.1)
        QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(0, 30))
        self.assertEqual(values[-1], 0)
        widget.close()
        widget.deleteLater()

    def test_main_quick_action_waits_for_waveform_and_updates_without_dialogs(self):
        with patch.dict(os.environ, {'APPDATA': str(self.folder / 'config')}):
            window = MediaCategorizer()
            window.showNormal()
            window.files, window.current_index = [self.path], 0
            window.show_current_file()
            with patch('media_categorizer_v4_5.QMessageBox.information') as info, \
                 patch('media_categorizer_v4_5.QMessageBox.warning') as warning:
                window.remove_audio_btn.click()
                self.assertTrue(window.file_reservations.is_busy(self.path))
                self.assertFalse(window.edit_photo_btn.isEnabled())
                self.assertTrue(window.render_progress.isVisible())
                deadline = time.monotonic() + 30
                while window.active_audio_removal and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertIsNone(window.active_audio_removal)
                self.assertFalse(info.called)
                self.assertFalse(warning.called)
                self.assertFalse(window.file_reservations.is_busy(self.path))
                self.assertFalse(window.render_progress.isVisible())
                self.assertEqual(window.waveform.message, 'В видео нет звуковой дорожки')
                self.assertFalse(probe(self.path)['audio'])
            window.close()
            window.thread_pool.waitForDone()
            self.app.processEvents()
            window.deleteLater()
