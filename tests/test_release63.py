import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import array
import math
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QRect, QPoint, QMimeData, QUrl, QEvent
from PySide6.QtGui import QImage, QColor, QDropEvent, QDragEnterEvent
from PySide6.QtWidgets import QApplication
from media_categorizer.video_export import ExportOptions, VideoExport, signature, probe
from media_categorizer.video_editor import VideoEditor
from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.library import LibraryDialog
from media_categorizer.appearance import AppearanceDialog
from media_categorizer.ui import apply_theme, THEME_NAMES
from media_categorizer_v4_5 import MediaCategorizer
from test_video_editor import ffmpeg, rms


class Release63Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.existing_windows = set(self.app.topLevelWidgets())
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'APPDATA':str(self.root/'config')})
        self.env.start()

    def tearDown(self):
        for window in self.app.topLevelWidgets():
            if window not in self.existing_windows:
                window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        apply_theme('dark', 'normal', False)
        self.app.processEvents()
        self.env.stop()
        self.temp.cleanup()

    def photo(self, name='photo.png'):
        path = self.root/name
        image = QImage(320, 180, QImage.Format_RGB32)
        image.fill(QColor('#d07030'))
        image.save(str(path))
        return path

    def video(self, audio=True):
        path = self.root/'video.mp4'
        args = ['-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=3']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-f', 'lavfi', '-i', 'sine=frequency=880:duration=3', '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:a', 'aac']
        ffmpeg(*args, '-c:v', 'libx264', path)
        return path

    def spin(self, predicate, timeout=15):
        deadline = time.monotonic()+timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(predicate())

    def test_middle_cuts_keep_audio_tracks_and_expected_frames(self):
        path = self.video()
        original = self.root/'original.mp4'
        shutil.copyfile(path, original)
        options = ExportOptions('source', 30, 'high', ((.5, 1), (.8, 1.5), (2, 2.5), (8, 9)))
        self.assertEqual(options.segments(0, 3), [(0, .5), (1.5, 2), (2.5, 3)])
        VideoExport().run(path, 0, 3, 1, signature(path), options=options)
        info = probe(path)
        self.assertAlmostEqual(info['duration'], 1.5, delta=.04)
        self.assertEqual(len(info['audio']), 2)
        # Frame after the splice must come from 1.7s, not the removed interval.
        def frame(p, seconds):
            return ffmpeg('-ss', seconds, '-i', p, '-frames:v', '1', '-vf', 'scale=80:46', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-')
        actual, expected, removed = frame(path, .7), frame(original, 1.7), frame(original, .7)
        distance = lambda a, b: sum(abs(x-y) for x,y in zip(a,b))/len(a)
        self.assertLess(distance(actual, expected), 8)
        self.assertGreater(distance(actual, removed), distance(actual, expected)*2)

    def test_rotation_flip_square_and_silent_cut_export(self):
        path = self.video(False)
        options = ExportOptions('720', 30, 'balanced', ((1, 2),), 90, True, '1:1')
        VideoExport().run(path, 0, 3, 1, signature(path), options=options)
        info = probe(path)
        self.assertEqual((info['width'], info['height']), (720,720))
        self.assertAlmostEqual(info['duration'], 2, delta=.05)
        self.assertEqual(info['audio'], [])
        self.assertEqual(list(self.root.glob('*.part')), [])

    def test_normalization_raises_quiet_audio_and_limits_peaks(self):
        path = self.root/'quiet.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=size=160x90:duration=3', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', '-af', 'volume=0.05', '-c:v', 'libx264', '-c:a', 'aac', path)
        before = rms(path)
        VideoExport().run(path, 0, 3, 2, signature(path), options=ExportOptions('source', normalize=True))
        self.assertGreater(rms(path), before*5)
        samples = array.array('f')
        samples.frombytes(ffmpeg('-i', path, '-map', '0:a:0', '-f', 'f32le', '-ac', '1', '-'))
        self.assertLess(max(abs(v) for v in samples), .99)

    def test_removing_entire_video_never_replaces_source(self):
        path = self.video(False)
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            VideoExport().run(path, 0, 3, 1, signature(path), options=ExportOptions(cuts=((0,3),)))
        self.assertEqual(path.read_bytes(), original)

    def test_video_editor_tools_frame_step_and_zoom(self):
        editor = VideoEditor(self.video())
        try:
            editor.range_timeline.set_position(1000)
            editor.range_timeline.set_zoom(8)
            self.assertEqual(editor.range_timeline.zoom, 8)
            editor.range_timeline.set_offset(.5)
            self.assertGreater(editor.range_timeline.offset, 0)
            editor.splits=[.5,1.5]
            editor.refresh_cuts()
            editor.range_timeline.selected=1
            editor.remove_fragment()
            self.assertEqual(editor.options().segments(0,3), [(0,.5),(1.5,3)])
            editor.rotation.setCurrentIndex(1)
            editor.crop_ratio.setCurrentIndex(2)
            self.assertLess(editor.video.sceneRect().width(), editor.video.sceneRect().height())
            self.assertAlmostEqual(editor.start.singleStep(), 1/30)
            editor.undo_montage()
            self.assertEqual(editor.cuts, [])
        finally:
            from PySide6.QtWidgets import QMessageBox
            with patch.object(QMessageBox, "question", return_value=QMessageBox.Discard):
                editor.reject()
            editor.deleteLater()

    def test_precise_crop_zoom_and_straighten_without_blank_corners(self):
        path = self.photo()
        editor = PhotoEditor(path)
        try:
            editor.canvas.set_zoom(4)
            self.assertEqual(editor.canvas.image.size(), editor.original.size())
            for spin, value in zip(editor.crop_fields, (12, 13, 100, 80)):
                spin.setValue(value)
            self.assertEqual(editor.canvas.selection, QRect(12,13,100,80))
            editor.crop()
            self.assertEqual(editor.canvas.image.size().toTuple(), (100,80))
            editor.angle.setValue(12.5)
            editor.straighten()
            result = editor.canvas.image
            for x,y in ((0,0),(result.width()-1,0),(0,result.height()-1),(result.width()-1,result.height()-1)):
                self.assertEqual(result.pixelColor(x,y).alpha(),255)
            editor.undo()
            self.assertEqual(editor.canvas.image.size().toTuple(), (100,80))
            editor.save()
            self.assertEqual(QImage(str(path)).size().toTuple(), (100,80))
        finally:
            editor.deleteLater()

    def test_drop_filters_deduplicates_and_respects_busy_queue(self):
        path = self.photo()
        (self.root/'ignore.txt').write_text('ignore')
        main = MediaCategorizer()
        try:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(self.root)), QUrl.fromLocalFile(str(path))])
            enter = QDragEnterEvent(QPoint(20,20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            main.dragEnterEvent(enter)
            self.assertTrue(enter.isAccepted())
            drop = QDropEvent(QPoint(20,20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            main.dropEvent(drop)
            self.assertTrue(drop.isAccepted())
            self.assertEqual(main.files, [path])
            main.file_operation_queue.append({'test':True})
            busy = QDropEvent(QPoint(20,20), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            main.dropEvent(busy)
            self.assertFalse(busy.isAccepted())
        finally:
            main.file_operation_queue.clear()
            main.close()

    def test_library_auto_refresh_preserves_selection_on_rename_and_add(self):
        path = self.photo()
        main = MediaCategorizer()
        main.settings['last_folder'] = str(self.root)
        library = LibraryDialog(main)
        library.show()
        try:
            self.spin(lambda: library.worker is None and len(library.records)==1)
            library.table.selectRow(0)
            renamed = path.with_name('renamed.png')
            path.rename(renamed)
            self.photo('added.png')
            self.spin(lambda: len(library.records)==2 and renamed in library.selected())
            self.assertEqual(library.selected(), [renamed])
            renamed.unlink()
            self.spin(lambda: len(library.records)==1)
            self.assertEqual(library.selected(), [])
        finally:
            library.reject()
            self.spin(lambda: library.worker is None)
            main.close()

    def test_appearance_size_labels_persist_across_themes(self):
        main = MediaCategorizer()
        dialog = AppearanceDialog(main)
        try:
            dialog.size.setCurrentIndex(dialog.size.findData('large'))
            dialog.labels.setChecked(True)
            for theme in THEME_NAMES:
                main.set_theme(theme)
                self.assertEqual(main.open_file_btn.text(), 'Файл')
                self.assertGreater(main.open_file_btn.height(), 38)
                self.assertEqual(main.settings['ui_size'], 'large')
            dialog.size.setCurrentIndex(dialog.size.findData('compact'))
            dialog.labels.setChecked(False)
            self.assertLess(main.open_file_btn.height(), 38)
            self.assertEqual(main.open_file_btn.text(), '')
        finally:
            dialog.accept()
            main.close()
