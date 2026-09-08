import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QRect, QPoint, QPointF, QThreadPool, Qt
from PySide6.QtGui import QImage, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QPushButton

from media_categorizer_v4_5 import FileOperationTask, MediaCategorizer


class TestWindow(MediaCategorizer):
    def _preload_window(self):
        pass

    def _preload_video_window(self):
        pass


class HeldQueue:
    def __init__(self):
        self.tasks = []

    def start(self, task):
        self.tasks.append(task)


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"APPDATA": str(self.root / "config")})
        self.env.start()
        self.messages = []
        self.dialogs = [patch("media_categorizer_v4_5.QMessageBox." + name,
                              side_effect=lambda *args: self.messages.append(args))
                        for name in ("information", "warning", "critical")]
        for dialog in self.dialogs:
            dialog.start()
        self.input = self.root / "input"
        self.input.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        for name in ("a.png", "b.png"):
            image = QImage(400, 300, QImage.Format_RGB32)
            image.fill(Qt.red)
            self.assertTrue(image.save(str(self.input / name)))
        self.window = TestWindow()
        self.pool = HeldQueue()
        self.window.thread_pool = self.pool
        self.window.categories = [
            {"name": "COPY", "shortcut": "1", "action": "copy", "destination": str(self.output)},
            {"name": "MOVE", "shortcut": "2", "action": "move", "destination": str(self.output)},
            {"name": "TAG", "shortcut": "3", "action": "rename", "destination": ""},
        ]
        self.window.build_category_buttons()
        self.window.show()
        self.app.processEvents()
        self.window.showNormal()
        self.window.resize(1500, 900)
        self.window.load_folder(self.input)
        self.app.processEvents()

    def tearDown(self):
        while self.pool.tasks:
            self.pool.tasks.pop(0).run()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        for dialog in self.dialogs:
            dialog.stop()
        self.env.stop()
        self.temp.cleanup()

    def complete_next(self):
        self.pool.tasks.pop(0).run()
        self.app.processEvents()

    def test_theme_round_trip_preserves_categories(self):
        from media_categorizer.settings import load_settings
        original = self.app.palette().window().color()
        self.window.theme_actions["light"].trigger()
        self.assertNotEqual(original, self.app.palette().window().color())
        saved = load_settings()
        self.assertEqual(saved['theme'], 'light')
        self.assertEqual(saved['categories'], self.window.categories)
        self.window.close()
        other = TestWindow()
        try:
            self.assertEqual(self.app.property('theme'), 'light')
            self.assertEqual(other.theme_btn.icon_name, 'moon')
            other.theme_actions["dark"].trigger()
            self.assertEqual(load_settings()['theme'], 'dark')
        finally:
            other.close()
            other.deleteLater()

    def test_custom_switch_keyboard_and_compact_layout(self):
        from PySide6.QtTest import QTest
        self.window.multi_btn.setFocus()
        QTest.keyClick(self.window.multi_btn, Qt.Key_Space)
        self.assertTrue(self.window.multi_btn.isChecked())
        self.assertTrue(self.window.apply_tags_btn.isVisible())
        self.window.resize(1400, 720)
        self.app.processEvents()
        self.assertLessEqual(self.window.width(), 1400)
        self.assertGreater(self.window.media_stack.height(), 300)
        for panel in (self.window.top_widget, self.window.tools_widget):
            for child in panel.findChildren(QPushButton):
                if child.isVisible():
                    self.assertTrue(panel.rect().contains(child.geometry()), child.toolTip())

    def test_editor_reloads_image_and_rejects_stale_preloads(self):
        from media_categorizer.photo_editor import PhotoEditor
        path = self.window.current_file
        stale = QImage(self.window.current_image)
        def edit(dialog):
            self.assertTrue(self.window.file_reservations.is_busy(path))
            self.assertFalse(self.window.edit_photo_btn.isEnabled())
            dialog.canvas.selection = QRect(10, 10, 100, 80)
            dialog.save()
            return dialog.result()
        with patch.object(PhotoEditor, 'exec', edit):
            self.window.edit_photo()
        self.assertFalse(self.window.file_reservations.is_busy(path))
        self.assertTrue(self.window.edit_photo_btn.isEnabled())
        self.assertEqual(self.window.current_image.width(), 100)
        self.window._on_image_preloaded(str(path), stale, 0)
        self.window._on_thumbnail_loaded(str(path), stale, 0)
        self.assertEqual(self.window._take_cached_image(path).width(), 100)
        self.assertEqual(QImage(str(path)).height(), 80)

    def test_busy_source_blocks_buttons_and_keyboard_actions(self):
        original = self.window.current_file
        self.window.process_categories(["COPY"])
        self.window.navigate_to_path(original)
        self.assertFalse(self.window.category_buttons["TAG"].isEnabled())
        self.window.activate_category("TAG")
        self.window.activate_category("COPY")
        self.assertEqual(len(self.pool.tasks), 1)
        self.assertTrue(original.exists())
        self.assertFalse(original.with_name("TAG_a.png").exists())
        self.complete_next()
        self.assertTrue(self.window.category_buttons["TAG"].isEnabled())
        self.assertFalse(self.window.file_reservations.is_busy(original))

    def test_undo_is_blocked_when_renamed_file_is_copying(self):
        self.window.process_categories(["TAG"])
        renamed = self.input / "TAG_a.png"
        self.window.navigate_to_path(renamed)
        self.window.process_categories(["COPY"])
        self.assertFalse(self.window.undo_btn.isEnabled())
        self.window.undo_last_action()
        self.assertTrue(renamed.exists())
        self.assertEqual(len(self.window.undo_stack), 1)
        self.complete_next()
        self.window.undo_last_action()  # Remove completed copy.
        self.assertFalse((self.output / renamed.name).exists())
        self.window.undo_last_action()  # Restore original name.
        self.assertTrue((self.input / "a.png").exists())
        self.assertFalse(renamed.exists())

    def test_failed_move_restores_source_and_releases_reservations(self):
        original = self.window.current_file
        self.window.process_categories(["MOVE"])
        target = self.window.active_file_operation["target"]
        target.write_bytes(b"external file")
        self.complete_next()
        self.assertEqual(target.read_bytes(), b"external file")
        self.assertIn(original, self.window.files)
        self.assertFalse(self.window.file_reservations.is_busy(original, target))
        self.assertEqual(self.window.current_file.name, "b.png")
        self.assertFalse(self.window.undo_stack)

    def test_move_and_undo_restore_file_and_position(self):
        original = self.window.current_file
        content = original.read_bytes()
        self.window.process_categories(["MOVE"])
        self.complete_next()
        self.assertFalse(original.exists())
        self.window.undo_last_action()
        self.assertEqual(original.read_bytes(), content)
        self.assertEqual(self.window.current_file, original)
        self.assertFalse((self.output / "a.png").exists())

    def test_undo_does_not_overwrite_new_file_at_original_path(self):
        self.window.process_categories(["MOVE"])
        self.complete_next()
        original = self.input / "a.png"
        original.write_bytes(b"new file")
        self.window.undo_last_action()
        self.assertEqual(original.read_bytes(), b"new file")
        self.assertTrue((self.output / "a.png").exists())
        self.assertEqual(len(self.window.undo_stack), 1)

    def test_two_queued_operations_keep_independent_reservations(self):
        first = self.window.current_file
        self.window.process_categories(["COPY"])
        second = self.window.current_file
        self.window.process_categories(["COPY"])
        self.assertEqual(len(self.window.file_operation_queue), 1)
        self.complete_next()
        self.assertFalse(self.window.file_reservations.is_busy(first))
        self.assertTrue(self.window.file_reservations.is_busy(second))
        self.complete_next()
        self.assertFalse(self.window.file_reservations.is_busy(second))
        self.assertEqual(len(self.window.undo_stack), 2)

    def test_real_background_queue_finishes_and_unlocks(self):
        self.window.thread_pool = QThreadPool.globalInstance()
        original = self.window.current_file
        self.window.process_categories(["COPY"])
        deadline = time.monotonic() + 5
        while self.window.active_file_operation is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(self.window.active_file_operation)
        self.assertEqual((self.output / original.name).read_bytes(), original.read_bytes())
        self.assertFalse(self.window.file_reservations.is_busy(original))
        self.window.undo_last_action()
        self.assertFalse((self.output / original.name).exists())

    def test_thread_start_failure_releases_file_and_restores_move(self):
        original = self.window.current_file
        with patch.object(self.pool, "start", side_effect=RuntimeError("worker unavailable")):
            self.window.process_categories(["MOVE"])
            self.app.processEvents()
        self.assertIn(original, self.window.files)
        self.assertIsNone(self.window.active_file_operation)
        self.assertFalse(self.window.file_reservations.is_busy(original))
        self.assertTrue(original.exists())

    def test_worker_progress_accepts_files_larger_than_two_gib(self):
        task = FileOperationTask("large", "copy", self.input / "a.png", self.output / "a.png")
        received = []
        task.signals.progress.connect(lambda _id, done, total: received.append((done, total)))
        task.signals.progress.emit("large", 5 * 1024**3, 6 * 1024**3)
        self.assertEqual(received, [(5 * 1024**3, 6 * 1024**3)])

    def send_wheel(self, canvas, local, delta=120):
        event = QWheelEvent(QPointF(local), QPointF(canvas.mapToGlobal(local)), QPoint(), QPoint(0, delta),
                            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        self.app.sendEvent(canvas.viewport() if hasattr(canvas,"viewport") else canvas, event)
        self.app.processEvents()

    def test_photo_wheel_anchor_drag_and_reset(self):
        canvas = self.window.image_label
        self.window.set_zoom_percent(200)
        anchor = QPoint(canvas.width() * 3 // 5, canvas.height() // 2)
        area, source = canvas._area_and_source()
        x, y, width, height = canvas.view.rectangle(*area, *source)
        image_point = ((anchor.x() - x) / width, (anchor.y() - y) / height)
        self.send_wheel(canvas, anchor)
        self.assertEqual(self.window.zoom_slider.value(), 210)
        x, y, width, height = canvas.view.rectangle(*area, *source)
        self.assertAlmostEqual((anchor.x() - x) / width, image_point[0])
        self.assertAlmostEqual((anchor.y() - y) / height, image_point[1])
        before = canvas.view.offset_x
        for event_type, local, button, buttons in (
            (QEvent.MouseButtonPress, anchor, Qt.LeftButton, Qt.LeftButton),
            (QEvent.MouseMove, anchor + QPoint(10, 0), Qt.NoButton, Qt.LeftButton),
            (QEvent.MouseButtonRelease, anchor + QPoint(10, 0), Qt.LeftButton, Qt.NoButton),
        ):
            event = QMouseEvent(event_type, QPointF(local), QPointF(canvas.mapToGlobal(local)),
                                button, buttons, Qt.NoModifier)
            self.app.sendEvent(canvas, event)
        self.assertAlmostEqual(canvas.view.offset_x - before, 10)
        self.window.reset_zoom()
        self.assertEqual(canvas.view.offset_x, 0)
        self.assertEqual(canvas.view.percent, 100)

    def test_video_wheel_uses_host_coordinates(self):
        host, canvas = self.window.video_host, self.window.video_canvas
        self.window.media_pages.setCurrentWidget(host)
        canvas.show()
        self.app.processEvents()
        canvas.set_video_geometry(QImage(1600, 900, QImage.Format_RGB32).size())
        self.window.set_zoom_percent(200)
        anchor_host = QPoint(host.width() * 3 // 5, host.height() // 2)
        anchor_local = canvas.mapFromParent(anchor_host)
        area, source = canvas._area_and_source()
        x, y, width, height = canvas.view.rectangle(*area, *source)
        fraction = (anchor_host.x() - x) / width
        self.send_wheel(canvas, anchor_local)
        self.assertEqual(self.window.zoom_slider.value(), 210)
        x, y, width, height = canvas.view.rectangle(*area, *source)
        self.assertAlmostEqual((anchor_host.x() - x) / width, fraction)
        self.window.reset_zoom()
        self.assertLessEqual(canvas.width(), host.width())
        self.assertLessEqual(canvas.height(), host.height())


if __name__ == "__main__":
    unittest.main()
