import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QEvent, QRect
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QScrollArea, QPushButton
from PySide6.QtTest import QTest
from media_categorizer.category_widgets import CategoryScrollArea
from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.ui import apply_theme, TimeSpinBox
from test_gui import TestWindow, HeldQueue


class Interface64Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'APPDATA':str(self.root/'settings')})
        self.env.start()
        self.windows=[]

    def tearDown(self):
        for window in self.windows:
            window.deleteLater()
        self.app.sendPostedEvents(None,QEvent.DeferredDelete)
        self.env.stop()
        self.temp.cleanup()

    def test_disabling_focused_categories_preserves_scroll_position(self):
        # First reproduce Qt's default behavior, then verify our specialized area.
        values=[]
        for cls in (QScrollArea,CategoryScrollArea):
            area=cls()
            self.windows.append(area)
            content=QWidget()
            row=QHBoxLayout(content)
            buttons=[QPushButton('Категория '+str(i)) for i in range(20)]
            for button in buttons:
                button.setFixedWidth(160)
                row.addWidget(button)
            area.setWidget(content)
            area.resize(420,100)
            area.show()
            area.activateWindow()
            self.app.processEvents()
            area.horizontalScrollBar().setValue(0)
            buttons[0].setFocus()
            self.app.processEvents()
            for button in buttons[:15]:
                button.setEnabled(False)
            self.app.processEvents()
            values.append(area.horizontalScrollBar().value())
            for button in buttons:
                button.setEnabled(True)
            buttons[0].setFocus()
            for _ in range(10):
                QTest.keyClick(QApplication.focusWidget(),Qt.Key_Tab)
            self.app.processEvents()
            self.assertGreater(area.horizontalScrollBar().value(),0,'Explicit keyboard navigation must still scroll')
            area.close()
        self.assertGreater(values[0],0,'Regression fixture must reproduce the original Qt scrolling')
        self.assertEqual(values[1],0)

    def test_photo_tool_cards_guides_and_fullscreen_restore(self):
        image=QImage(400,300,QImage.Format_RGB32)
        image.fill(Qt.green)
        path=self.root/'photo.png'
        image.save(str(path))
        editor=PhotoEditor(path)
        self.windows.append(editor)
        editor.show()
        self.app.processEvents()
        editor.ratio_cards.buttons[1].click()
        self.assertEqual(editor.aspect.currentData(),1)
        self.assertEqual(editor.canvas.selection.width(),editor.canvas.selection.height())
        editor.guides.setCurrentIndex(2)
        self.assertEqual(editor.canvas.guides,'dense')
        editor.tools.setCurrentIndex(2)
        editor.angle.setValue(3.25)
        self.assertTrue(editor.canvas.horizon_visible)
        self.assertEqual(editor.canvas.horizon,3.25)
        parent=editor.preview_host.parentWidget()
        editor.preview_host.toggle_fullscreen()
        self.app.processEvents()
        self.assertIsNotNone(editor.preview_host.fullscreen)
        editor.preview_host.toggle_fullscreen()
        self.app.processEvents()
        self.assertIs(editor.preview_host.parentWidget(),parent)
        editor.undo()
        editor.canvas.clear_selection()
        editor.reject()

    def test_floating_controls_fade_and_return_on_interaction(self):
        image=QImage(400,300,QImage.Format_RGB32)
        image.fill(Qt.blue)
        path=self.root/'photo.png'
        image.save(str(path))
        editor=PhotoEditor(path)
        self.windows.append(editor)
        editor.show()
        host=editor.preview_host
        self.app.processEvents()
        host.effect.setOpacity(0)
        host.finish_fade()
        self.assertFalse(host.bar.isHidden())
        host.reveal()
        self.assertFalse(host.bar.isHidden())
        self.assertEqual(host.effect.opacity(),1)
        editor.reject()

    def test_time_display_keeps_frame_step_precision(self):
        spin=TimeSpinBox()
        self.windows.append(spin)
        spin.setDecimals(6)
        spin.setSingleStep(1/30)
        for _ in range(30):
            spin.stepUp()
        self.assertAlmostEqual(spin.value(),1,delta=.00005)
        self.assertEqual(spin.textFromValue(1.234567),spin.locale().toString(1.235,'f',3))

    def test_main_toolbar_fits_large_mode(self):
        main=TestWindow()
        self.windows.append(main)
        main.showNormal()
        self.app.processEvents()
        main.showNormal()
        main.resize(1100,760)
        apply_theme('purple','large',True)
        self.app.processEvents()
        self.assertLessEqual(main.width(),1100)
        self.assertFalse(isinstance(main.top_widget,QScrollArea))
        self.assertTrue(main.theme_btn.isVisible())
        self.assertLessEqual(main.theme_btn.geometry().right(),main.top_widget.width())
        main.close()
        apply_theme('dark','normal',False)
