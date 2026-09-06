import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtGui import QImage, QColor, QPalette
from PySide6.QtWidgets import QApplication, QTextEdit
from media_categorizer.ui import COLORS, THEME_NAMES, apply_theme, ToggleSwitch
from media_categorizer.category_widgets import CategoryButton
from media_categorizer.settings import load_settings, normalize_categories
from media_categorizer.timeline import RangeTimeline
from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.updates import UpdatePrompt
from media_categorizer_v4_5 import MediaCategorizer


def luminance(hex_color):
    c = QColor(hex_color)
    rgb = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in (c.redF(),c.greenF(),c.blueF())]
    return sum(v*k for v,k in zip(rgb,(.2126,.7152,.0722)))


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        apply_theme('dark')

    def test_palette_text_contrast_and_complete_roles(self):
        self.assertEqual(set(THEME_NAMES), set(COLORS))
        for name, colors in COLORS.items():
            self.assertEqual(set(colors), set(COLORS['dark']))
            for background in ('bg','panel','selected','border'):
                l1,l2 = sorted((luminance(colors['text']),luminance(colors[background])))
                self.assertGreaterEqual((l2+.05)/(l1+.05), 4.5, (name,background))
            apply_theme(name)
            self.assertEqual(self.app.palette().color(QPalette.Window).name(), colors['bg'])
            self.assertEqual(self.app.palette().color(QPalette.Link).name(), colors['accent'])
            edit = QTextEdit()
            self.assertEqual(edit.palette().color(QPalette.Text).name(), colors['text'])
            edit.deleteLater()

    def test_selection_persists_and_reloads_all_themes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APPDATA':directory}):
            main = MediaCategorizer()
            try:
                for theme in THEME_NAMES:
                    main.theme_actions[theme].trigger()
                    self.assertEqual(load_settings()['theme'], theme)
                    self.assertTrue(main.theme_actions[theme].isChecked())
                    self.assertEqual(sum(a.isChecked() for a in main.theme_actions.values()), 1)
                main.set_theme('purple')
                other = MediaCategorizer()
                self.assertEqual(self.app.property('theme'), 'purple')
                self.assertTrue(other.theme_actions['purple'].isChecked())
                other.close()
            finally:
                main.close()

    def test_category_theme_color_changes_but_custom_color_remains(self):
        auto = CategoryButton({'name':'Auto'}, 'Action')
        custom = CategoryButton({'name':'Custom','color':'#ff0000'}, 'Action')
        for theme in THEME_NAMES:
            apply_theme(theme)
            self.assertIn(COLORS[theme]['accent'], auto.styleSheet())
            self.assertIn('#ff0000', custom.styleSheet())
        self.assertEqual(normalize_categories([{'name':'Auto','color':'auto'}])[0]['color'], 'auto')
        auto.deleteLater()
        custom.deleteLater()

    def test_editors_and_update_prompt_render_in_every_theme(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APPDATA':directory}):
            path = Path(directory)/'photo.png'
            image = QImage(80,60,QImage.Format_RGB32)
            image.fill(QColor('gray'))
            image.save(str(path))
            main = MediaCategorizer()
            photo = PhotoEditor(path, main)
            timeline = RangeTimeline(10)
            timeline.resize(400,152)
            timeline.waveform = QImage(100,20,QImage.Format_ARGB32)
            timeline.waveform.fill(QColor(255,255,255,80))
            prompt = UpdatePrompt(main, {'version':'99.0.0','notes':'New version'})
            toggle = ToggleSwitch('Switch')
            toggle.setChecked(True)
            try:
                for theme in THEME_NAMES:
                    apply_theme(theme)
                    for widget in (photo,timeline,prompt,toggle):
                        self.assertFalse(widget.grab().isNull(), theme)
            finally:
                photo.reject()
                prompt.reject()
                timeline.close()
                toggle.close()
                main.close()

    def test_unknown_saved_theme_falls_back_to_blue(self):
        apply_theme('missing')
        self.assertEqual(self.app.property('theme'), 'dark')
