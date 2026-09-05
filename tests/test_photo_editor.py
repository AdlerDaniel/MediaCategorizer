import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from media_categorizer.photo_editor import PhotoEditor, CropCanvas, fingerprint, load_photo, replace_photo

class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'фото.png'
        image = QImage(80, 60, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        for y in range(60):
            for x in range(80):
                image.setPixelColor(x, y, QColor(x*3, y*4, 90, 180))
        self.assertTrue(image.save(str(self.path)))
        self.original = self.path.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def test_reflections_crop_and_atomic_replacement(self):
        editor = PhotoEditor(self.path)
        initial = QImage(editor.original)
        editor.flip(True)
        self.assertEqual(editor.canvas.image.pixelColor(0, 0), initial.pixelColor(79, 0))
        editor.flip(False)
        self.assertEqual(editor.canvas.image.pixelColor(0, 0), initial.pixelColor(79, 59))
        editor.canvas.selection = QRect(10, 12, 30, 20)
        expected = editor.canvas.image.pixelColor(10, 12)
        editor.save()  # Pending crop is included when Save is pressed.
        self.assertEqual(editor.result(), PhotoEditor.Accepted)
        saved = QImage(str(self.path))
        self.assertEqual((saved.width(), saved.height()), (30, 20))
        self.assertEqual(saved.pixelColor(0, 0), expected)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        editor.deleteLater()

    def test_reset_and_cancel_leave_original_untouched(self):
        editor = PhotoEditor(self.path)
        editor.flip(True)
        editor.reset()
        self.assertEqual(editor.canvas.image, editor.original)
        self.assertFalse(editor.save_btn.isEnabled())
        editor.flip(False)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Cancel):
            editor.reject()
            self.assertTrue(editor.dirty)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Discard):
            editor.reject()
        self.assertEqual(self.path.read_bytes(), self.original)
        editor.deleteLater()

    def test_failed_encoder_preserves_original_and_removes_staging(self):
        image, fmt = load_photo(self.path)
        with patch('media_categorizer.photo_editor.QImageWriter') as writer:
            writer.return_value.write.return_value = False
            writer.return_value.errorString.return_value = 'Disk full'
            with self.assertRaises(OSError):
                replace_photo(self.path, image, fmt, fingerprint(self.path))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_external_change_blocks_replacement(self):
        image, fmt = load_photo(self.path)
        expected = fingerprint(self.path)
        replacement = QImage(3, 4, QImage.Format_RGB32)
        replacement.fill(Qt.green)
        replacement.save(str(self.path))
        external = self.path.read_bytes()
        with self.assertRaises(ValueError):
            replace_photo(self.path, image, fmt, expected)
        self.assertEqual(self.path.read_bytes(), external)

    def test_jpeg_keeps_format_and_requested_dimensions(self):
        jpeg = self.path.with_suffix('.jpg')
        QImage(str(self.path)).save(str(jpeg))
        image, fmt = load_photo(jpeg)
        replace_photo(jpeg, image.copy(QRect(2, 3, 20, 15)), fmt, fingerprint(jpeg))
        self.assertTrue(jpeg.read_bytes().startswith(bytes([255, 216])))
        saved, saved_fmt = load_photo(jpeg)
        self.assertEqual(saved_fmt, fmt)
        self.assertEqual((saved.width(), saved.height()), (20, 15))

    def test_crop_maps_letterboxed_preview_to_source_pixels(self):
        canvas = CropCanvas(QImage(str(self.path)))
        canvas.resize(400, 400)  # 80x60 fits at y=50 with scale=5.
        canvas.show()
        self.app.processEvents()
        QTest.mousePress(canvas, Qt.LeftButton, pos=QPoint(350, 300))
        QTest.mouseRelease(canvas, Qt.LeftButton, pos=QPoint(50, 100))
        self.assertEqual(canvas.selection, QRect(10, 10, 60, 40))
        canvas.resize(800, 600)
        self.assertEqual(canvas.selection, QRect(10, 10, 60, 40))
        canvas.close()
        canvas.deleteLater()
