"""Capture only our own widgets, with isolated generated media and settings."""
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'windows'
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from media_categorizer_v4_5 import MediaCategorizer, CategoriesDialog
from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.library import LibraryDialog
from media_categorizer.batch import BatchDialog
from media_categorizer.updates import UpdatesDialog
from media_categorizer.ui import apply_theme

app = QApplication([])
output = Path(__file__).resolve().parents[1] / 'build/qa'
output.mkdir(exist_ok=True, parents=True)

def settle(condition=lambda: False, timeout=.2):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    os.environ['APPDATA'] = str(folder / 'settings')
    image = QImage(640, 480, QImage.Format_RGB32)
    for y in range(480):
        for x in range(640):
            image.setPixelColor(x, y, QColor(x*255//640, y*255//480, 140))
    for name in ('Закат.png', 'Отпуск.png', 'Семья.png'):
        image.save(str(folder / name))
    main = MediaCategorizer()
    main.load_folder(folder)
    main.setAttribute(Qt.WA_ShowWithoutActivating)
    main.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    main.showNormal()
    main.resize(1400, 900)
    photo = PhotoEditor(folder / 'Закат.png', main)
    photo.canvas.set_ratio(4/5)
    library = LibraryDialog(main)
    library.setAttribute(Qt.WA_ShowWithoutActivating)
    library.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    library.show()
    settle(lambda: library.worker is None and library.table.rowCount() == 3, 10)
    assert library.table.rowCount() == 3
    library.table.selectAll()
    batch = BatchDialog(library.selected(), main)
    batch.prepare()
    assert len(batch.plans) == 3
    categories = CategoriesDialog(main.categories, main)
    updates = UpdatesDialog(main)
    windows = [('main',main),('photo',photo),('library',library),('batch',batch),('categories',categories),('updates',updates)]
    for theme in ('dark', 'light'):
        apply_theme(theme)
        for name, widget in windows:
            widget.setAttribute(Qt.WA_ShowWithoutActivating)
            widget.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
            widget.show()
            settle()
            assert widget.grab().save(str(output / f'release6-{name}-{theme}.png'))
            if widget is not main:
                widget.hide()
    photo.canvas.clear_selection()
    for name, widget in reversed(windows):
        widget.close()
    main.thread_pool.waitForDone()
    settle()
print('Library, batch plan, photo editor, categories, About and both themes: OK')
