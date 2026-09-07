import csv
import hashlib
import json
import sys
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QIcon,
    QColor,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
    QTransform,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoSink, QtVideo, QMediaDevices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QColorDialog,
    QInputDialog,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QKeySequenceEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedLayout,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from media_categorizer.constants import (
    APP_NAME, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, SUPPORTED_EXTENSIONS,
    ACTION_LABELS, ACTIONS_WITH_DESTINATION, ACTIONS_WITH_RENAME,
    DEFAULT_TEMPLATE, TEMPLATE_PRESETS, IMAGE_PRELOAD_FORWARD, IMAGE_PRELOAD_BACKWARD,
    IMAGE_CACHE_MAX_ITEMS, IMAGE_CACHE_MAX_BYTES, VIDEO_SLOT_COUNT,
    VIDEO_PRELOAD_COUNT, VIDEO_SCAN_AHEAD,
)
from media_categorizer.settings import load_settings, save_settings, log_path, normalize_categories
from media_categorizer.naming import safe_filename, sanitize_category, filter_duplicate_tags, render_rename
from media_categorizer.file_operations import FileReservations, perform_file_operation, rename_no_replace
from media_categorizer.viewer import ImageCanvas, MediaViewport, VideoCanvas, VideoViewport


from media_categorizer.photo_editor import PhotoEditor
from media_categorizer.video_editor import VideoEditor
from media_categorizer.category_widgets import CategoryButton, CategoryStrip, CategoryScrollArea
from media_categorizer.editor_widgets import ElidedLabel, PreviewHost
from media_categorizer.ui import IconButton, ToggleSwitch, apply_theme, icon, COLORS, THEME_NAMES

RESERVED_SHORTCUTS = {
    QKeySequence(Qt.Key_Right).toString(QKeySequence.PortableText),
    QKeySequence(Qt.Key_Left).toString(QKeySequence.PortableText),
    QKeySequence("Ctrl+Z").toString(QKeySequence.PortableText),
    QKeySequence("Enter").toString(QKeySequence.PortableText),
    QKeySequence("Return").toString(QKeySequence.PortableText),
    QKeySequence("Space").toString(QKeySequence.PortableText),
    QKeySequence("F11").toString(QKeySequence.PortableText),
    QKeySequence("Esc").toString(QKeySequence.PortableText),
    QKeySequence("Ctrl+Left").toString(QKeySequence.PortableText),
    QKeySequence("Ctrl+Right").toString(QKeySequence.PortableText),
    QKeySequence("Alt+Left").toString(QKeySequence.PortableText),
    QKeySequence("Alt+Right").toString(QKeySequence.PortableText),
    QKeySequence("Ctrl++").toString(QKeySequence.PortableText),
    QKeySequence("Ctrl+=").toString(QKeySequence.PortableText),
    QKeySequence("Ctrl+-").toString(QKeySequence.PortableText),
    QKeySequence("Ctrl+0").toString(QKeySequence.PortableText),
}


def read_oriented_image(path: Path) -> QImage:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    return reader.read()


def rotation_degrees(rotation) -> int:
    if rotation is None:
        return 0
    try:
        value = int(rotation.value)
        if value in (0, 90, 180, 270):
            return value
    except Exception:
        pass
    text = str(getattr(rotation, "name", None) or rotation)
    if "270" in text:
        return 270
    if "180" in text:
        return 180
    if "90" in text:
        return 90
    return 0


def set_video_frame_rotation(frame: QVideoFrame, degrees: int) -> bool:
    """Set presentation rotation without touching/decompressing the pixel buffer."""
    degrees = int(degrees or 0) % 360
    if degrees not in (0, 90, 180, 270):
        degrees = (round(degrees / 90) * 90) % 360

    try:
        mapping = {
            0: QtVideo.Rotation.None_,
            90: QtVideo.Rotation.Clockwise90,
            180: QtVideo.Rotation.Clockwise180,
            270: QtVideo.Rotation.Clockwise270,
        }
        frame.setRotation(mapping[degrees])
        return True
    except Exception:
        pass

    # Compatibility fallback for older Qt/PySide builds.
    try:
        enum_cls = QVideoFrame.RotationAngle
        mapping = {
            0: enum_cls.Rotation0,
            90: enum_cls.Rotation90,
            180: enum_cls.Rotation180,
            270: enum_cls.Rotation270,
        }
        frame.setRotationAngle(mapping[degrees])
        return True
    except Exception:
        return False


def rotate_and_mirror(image: QImage, degrees: int, mirrored: bool) -> QImage:
    if image.isNull():
        return image
    if degrees % 360:
        transform = QTransform()
        transform.rotate(degrees % 360)
        image = image.transformed(transform, Qt.SmoothTransformation)
    if mirrored:
        image = image.mirrored(True, False)
    return image


def oriented_video_frame_image(frame) -> QImage:
    try:
        image = frame.toImage()
    except Exception:
        return QImage()
    if image.isNull():
        return image

    try:
        fmt = frame.surfaceFormat()
        surface_rotation = rotation_degrees(fmt.rotation()) if hasattr(fmt, "rotation") else 0
        surface_mirror = bool(fmt.isMirrored()) if hasattr(fmt, "isMirrored") else False
        image = rotate_and_mirror(image, surface_rotation, surface_mirror)
    except Exception:
        pass

    try:
        if hasattr(frame, "rotation"):
            frame_rotation = rotation_degrees(frame.rotation())
        elif hasattr(frame, "rotationAngle"):
            frame_rotation = rotation_degrees(frame.rotationAngle())
        else:
            frame_rotation = 0
        frame_mirror = bool(frame.mirrored()) if hasattr(frame, "mirrored") else False
        image = rotate_and_mirror(image, frame_rotation, frame_mirror)
    except Exception:
        pass

    return image


def format_millis(ms: int) -> str:
    ms = max(0, int(ms or 0))
    seconds = ms // 1000
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def human_size(value: int) -> str:
    value = max(0, int(value or 0))
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{value} Б"


def file_sha256(path: Path, chunk_size=8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def difference_hash(path: Path) -> int | None:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        return None
    image = image.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
        9, 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )
    value = 0
    bit = 0
    for y in range(8):
        for x in range(8):
            left = image.pixelColor(x, y).value()
            right = image.pixelColor(x + 1, y).value()
            if left > right:
                value |= 1 << bit
            bit += 1
    return value


def hamming_distance(a: int, b: int) -> int:
    return int(a ^ b).bit_count()



class ImageLoadSignals(QObject):
    loaded = Signal(str, QImage, int)


class ImageLoadTask(QRunnable):
    def __init__(self, path: Path, revision=0):
        super().__init__()
        self.path = Path(path)
        self.revision = revision
        self.signals = ImageLoadSignals()

    def run(self):
        image = read_oriented_image(self.path) if self.path.exists() else QImage()
        self.signals.loaded.emit(str(self.path), image, self.revision)


class DestinationEditor(QWidget):
    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.edit = QLineEdit(value)
        self.button = QPushButton("…")
        self.button.setFixedWidth(34)
        self.button.clicked.connect(self.browse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def browse(self):
        start = self.edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Папка назначения", start)
        if folder:
            self.edit.setText(folder)

    def text(self):
        return self.edit.text().strip()


class CategoriesDialog(QDialog):
    def __init__(self, categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Категории, клавиши и действия")
        self.resize(1160, 600)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Категория", "Горячая клавиша", "Действие", "Папка назначения", "Цвет", "Иконка"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)

        add_button = QPushButton("Добавить")
        delete_button = QPushButton("Удалить")
        up_button = QPushButton("Выше")
        down_button = QPushButton("Ниже")
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")

        add_button.clicked.connect(lambda: self.add_row())
        delete_button.clicked.connect(self.delete_row)
        up_button.clicked.connect(lambda: self.move_row(-1))
        down_button.clicked.connect(lambda: self.move_row(1))
        save_button.clicked.connect(self.try_accept)
        cancel_button.clicked.connect(self.reject)

        controls = QHBoxLayout()
        controls.addWidget(add_button)
        controls.addWidget(delete_button)
        controls.addWidget(up_button)
        controls.addWidget(down_button)
        controls.addStretch()
        controls.addWidget(cancel_button)
        controls.addWidget(save_button)

        info = QLabel(
            "Для «Переименовать» папка не нужна. Для перемещения/копирования укажите папку. "
            "В режиме нескольких тегов выбранные категории должны иметь одинаковое действие и папку назначения."
        )
        info.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.table)
        layout.addLayout(controls)
        self.profiles = dict(parent.settings.get('category_profiles', {})) if parent else {}
        profile_row = QHBoxLayout()
        self.profile_combo = QComboBox()
        self.refresh_profiles()
        load_profile = QPushButton('Загрузить набор')
        save_profile = QPushButton('Сохранить набор')
        delete_profile = QPushButton('Удалить набор')
        load_profile.clicked.connect(lambda: self.set_categories(self.profiles[self.profile_combo.currentText()]) if self.profile_combo.currentText() in self.profiles else None)
        save_profile.clicked.connect(self.save_profile)
        delete_profile.clicked.connect(self.delete_profile)
        for widget in (QLabel('Наборы категорий'), self.profile_combo, load_profile, save_profile, delete_profile):
            profile_row.addWidget(widget)
        layout.insertLayout(0, profile_row)
        self.set_categories(categories)

    def refresh_profiles(self):
        self.profile_combo.clear()
        self.profile_combo.addItems(sorted(self.profiles))

    def save_profile(self):
        name, ok = QInputDialog.getText(self, 'Набор категорий', 'Название набора:')
        if ok and name.strip():
            self.profiles[name.strip()] = self.snapshot()
            self.refresh_profiles()
            self.profile_combo.setCurrentText(name.strip())

    def delete_profile(self):
        self.profiles.pop(self.profile_combo.currentText(), None)
        self.refresh_profiles()

    def set_categories(self, categories):
        self.table.setRowCount(0)
        for item in categories:
            self.add_row(
                item.get("name", ""),
                item.get("shortcut", ""),
                item.get("action", "rename"),
                item.get("destination", ""),
                item.get("color", "auto"), item.get("icon", "tags"),
            )

    def add_row(self, name="", shortcut="", action="rename", destination="", color="auto", icon_name="tags"):
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem(name)
        self.table.setItem(row, 0, name_item)

        shortcut_editor = QKeySequenceEdit(QKeySequence(shortcut))
        shortcut_editor.setClearButtonEnabled(True)
        self.table.setCellWidget(row, 1, shortcut_editor)

        action_combo = QComboBox()
        for action_id, label in ACTION_LABELS.items():
            action_combo.addItem(label, action_id)
        selected = action_combo.findData(action)
        action_combo.setCurrentIndex(max(0, selected))
        self.table.setCellWidget(row, 2, action_combo)

        dest_editor = DestinationEditor(destination)
        self.table.setCellWidget(row, 3, dest_editor)
        action_combo.currentIndexChanged.connect(
            lambda _=0, combo=action_combo, editor=dest_editor: self._sync_destination_enabled(combo, editor)
        )
        self._sync_destination_enabled(action_combo, dest_editor)

        color_button = QPushButton('Цвет')
        color_button.setProperty('categoryColor', color)
        display_color = COLORS[QApplication.instance().property('theme') or 'dark']['accent'] if color == 'auto' else color
        color_button.setStyleSheet(f'border-bottom: 4px solid {display_color};')
        def choose_color():
            chosen = QColorDialog.getColor(QColor(COLORS[QApplication.instance().property('theme') or 'dark']['accent'] if color_button.property('categoryColor') == 'auto' else color_button.property('categoryColor')), self)
            if chosen.isValid():
                color_button.setProperty('categoryColor', chosen.name())
                color_button.setStyleSheet(f'border-bottom: 4px solid {chosen.name()};')
        def use_theme_color():
            color_button.setProperty('categoryColor', 'auto')
            color_button.setStyleSheet('border-bottom: 4px solid ' + COLORS[QApplication.instance().property('theme') or 'dark']['accent'] + ';')
        color_menu = QMenu(color_button)
        color_menu.addAction('Цвет темы').triggered.connect(use_theme_color)
        color_menu.addAction('Выбрать цвет…').triggered.connect(choose_color)
        color_button.setMenu(color_menu)
        self.table.setCellWidget(row, 4, color_button)
        icons = QComboBox()
        for label, value in [('Тег','tags'), ('Галочка','check'), ('Видео','film'), ('Фото','images'), ('Папка','folder-open'), ('Информация','info'), ('Редактировать','pencil')]:
            icons.addItem(label, value)
        icons.setCurrentIndex(max(0, icons.findData(icon_name)))
        self.table.setCellWidget(row, 5, icons)
        self.table.setCurrentCell(row, 0)
        if not name:
            self.table.editItem(name_item)

    def _sync_destination_enabled(self, combo, editor):
        editor.setEnabled(combo.currentData() in ACTIONS_WITH_DESTINATION)

    def delete_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def snapshot(self):
        data = []
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 0)
            shortcut_editor = self.table.cellWidget(row, 1)
            action_combo = self.table.cellWidget(row, 2)
            dest_editor = self.table.cellWidget(row, 3)
            name = name_item.text().strip() if name_item else ""
            shortcut = shortcut_editor.keySequence().toString(QKeySequence.PortableText)
            action = action_combo.currentData() if isinstance(action_combo, QComboBox) else "rename"
            destination = dest_editor.text() if isinstance(dest_editor, DestinationEditor) else ""
            data.append({
                "name": name,
                "shortcut": shortcut,
                "action": action,
                "destination": destination,
                "color": self.table.cellWidget(row, 4).property("categoryColor"),
                "icon": self.table.cellWidget(row, 5).currentData(),
            })
        return data

    def move_row(self, delta):
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        data = self.snapshot()
        data[row], data[target] = data[target], data[row]
        self.set_categories(data)
        self.table.setCurrentCell(target, 0)

    def try_accept(self):
        categories = self.snapshot()
        cleaned = []
        names = set()
        shortcuts = set()
        for item in categories:
            name = item["name"].strip()
            shortcut = QKeySequence(item["shortcut"]).toString(QKeySequence.PortableText)
            action = item["action"] if item["action"] in ACTION_LABELS else "rename"
            destination = item["destination"].strip()

            if not name:
                QMessageBox.warning(self, APP_NAME, "Название категории не может быть пустым.")
                return
            if name.casefold() in names:
                QMessageBox.warning(self, APP_NAME, f"Категория «{name}» указана дважды.")
                return
            names.add(name.casefold())

            if shortcut:
                if shortcut in RESERVED_SHORTCUTS:
                    QMessageBox.warning(self, APP_NAME, f"Клавиша «{shortcut}» зарезервирована интерфейсом.")
                    return
                if shortcut in shortcuts:
                    QMessageBox.warning(self, APP_NAME, f"Клавиша «{shortcut}» назначена двум категориям.")
                    return
                shortcuts.add(shortcut)

            if action in ACTIONS_WITH_DESTINATION and not destination:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    f"Для категории «{name}» с действием «{ACTION_LABELS[action]}» нужна папка назначения.",
                )
                return

            cleaned.append({
                "name": name,
                "shortcut": shortcut,
                "action": action,
                "destination": destination,
                "color": item.get("color", "auto"),
                "icon": item.get("icon", "tags"),
            })

        if not cleaned:
            QMessageBox.warning(self, APP_NAME, "Нужна хотя бы одна категория.")
            return
        self._cleaned = cleaned
        self.accept()

    def result_categories(self):
        return getattr(self, "_cleaned", self.snapshot())


class RenameTemplateDialog(QDialog):
    def __init__(self, template, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Шаблон переименования")
        self.resize(680, 300)

        self.preset = QComboBox()
        self.preset.addItems(TEMPLATE_PRESETS)
        self.edit = QLineEdit(template or DEFAULT_TEMPLATE)
        self.preview = QLabel()
        self.preview.setWordWrap(True)

        self.preset.currentTextChanged.connect(self.edit.setText)
        self.edit.textChanged.connect(self.update_preview)

        info = QLabel(
            "Доступные поля: {tags}, {category}, {original}, {stem}, {ext}, {index}, {date}, {time}. "
            "Расширение исходного файла всегда сохраняется."
        )
        info.setWordWrap(True)

        buttons = QHBoxLayout()
        cancel = QPushButton("Отмена")
        save = QPushButton("Сохранить")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.try_accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Готовый шаблон:"))
        layout.addWidget(self.preset)
        layout.addWidget(QLabel("Редактировать:"))
        layout.addWidget(self.edit)
        layout.addWidget(info)
        layout.addWidget(self.preview)
        layout.addStretch()
        layout.addLayout(buttons)
        self.update_preview()

    def update_preview(self):
        template = self.edit.text().strip() or DEFAULT_TEMPLATE
        values = {
            "tags": "GOOD_POST",
            "category": "GOOD",
            "original": "IMG_1234.mp4",
            "stem": "IMG_1234",
            "ext": ".mp4",
            "index": "0042",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H-%M-%S"),
        }
        try:
            result = template.format(**values)
            if not result.lower().endswith(".mp4"):
                result += ".mp4"
            self.preview.setText(f"Пример: {safe_filename(result)}")
        except Exception as exc:
            self.preview.setText(f"Ошибка шаблона: {exc}")

    def try_accept(self):
        template = self.edit.text().strip()
        if not template:
            QMessageBox.warning(self, APP_NAME, "Шаблон не может быть пустым.")
            return
        values = {
            "tags": "TAG", "category": "TAG", "original": "file.jpg",
            "stem": "file", "ext": ".jpg", "index": "0001",
            "date": "2026-09-04", "time": "12-00-00",
        }
        try:
            template.format(**values)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Некорректный шаблон:\n{exc}")
            return
        self._template = template
        self.accept()

    def result_template(self):
        return getattr(self, "_template", self.edit.text().strip() or DEFAULT_TEMPLATE)


class LogDialog(QDialog):
    def __init__(self, records, parent=None):
        super().__init__(parent)
        self.records = records
        self.setWindowTitle("Журнал обработки")
        self.resize(1000, 560)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Время", "Действие", "Исходный файл", "Результат", "Статус", "Комментарий"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)

        export_button = QPushButton("Экспорт CSV")
        close_button = QPushButton("Закрыть")
        export_button.clicked.connect(self.export_csv)
        close_button.clicked.connect(self.accept)

        row = QHBoxLayout()
        row.addWidget(QLabel(f"Показаны последние {len(records)} записей"))
        row.addStretch()
        row.addWidget(export_button)
        row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row)
        self.populate()

    def populate(self):
        self.table.setRowCount(0)
        for record in self.records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                record.get("time", ""),
                record.get("action", ""),
                record.get("source", ""),
                record.get("result", ""),
                record.get("status", ""),
                record.get("detail", ""),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        if self.table.rowCount():
            self.table.scrollToBottom()

    def export_csv(self):
        default = str(Path.home() / "MediaCategorizer_log.csv")
        filename, _ = QFileDialog.getSaveFileName(self, "Экспорт журнала", default, "CSV (*.csv)")
        if not filename:
            return
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=["time", "action", "source", "result", "status", "detail"])
                writer.writeheader()
                writer.writerows(self.records)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось экспортировать журнал:\n{exc}")



class ThumbnailSignals(QObject):
    loaded = Signal(str, QImage)


class ThumbnailTask(QRunnable):
    def __init__(self, path: Path, size=QSize(160, 96), revision=0):
        super().__init__()
        self.path = Path(path)
        self.size = QSize(size)
        self.revision = revision
        self.signals = ThumbnailSignals()

    def run(self):
        image = QImage()
        if self.path.exists() and self.path.suffix.lower() in IMAGE_EXTENSIONS:
            reader = QImageReader(str(self.path))
            reader.setAutoTransform(True)
            raw_size = reader.size()
            if raw_size.isValid() and not raw_size.isEmpty():
                scaled = raw_size.scaled(QSize(self.size.width() * 2, self.size.height() * 2), Qt.KeepAspectRatio)
                reader.setScaledSize(scaled)
            image = reader.read()
        self.signals.loaded.emit(str(self.path), image, self.revision)


class FileOperationSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(str, bool, str, str)


class FileOperationTask(QRunnable):
    def __init__(self, op_id: str, kind: str, source: Path, target: Path):
        super().__init__()
        self.op_id = op_id
        self.kind = kind
        self.source = Path(source)
        self.target = Path(target)
        self.signals = FileOperationSignals()

    def run(self):
        try:
            perform_file_operation(
                self.kind, self.source, self.target,
                lambda copied, total: self.signals.progress.emit(self.op_id, copied, total),
            )
        except Exception as exc:
            self.signals.finished.emit(self.op_id, False, str(self.target), str(exc))
        else:
            self.signals.finished.emit(self.op_id, True, str(self.target), "")


class DuplicateScanSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object, object)
    failed = Signal(str)


class DuplicateScanTask(QRunnable):
    VISUAL_THRESHOLD = 8

    def __init__(self, files):
        super().__init__()
        self.files = [Path(p) for p in files if Path(p).exists()]
        self.signals = DuplicateScanSignals()

    def run(self):
        try:
            total_steps = max(1, len(self.files) * 2)
            done = 0

            # Exact duplicates: size first, SHA-256 only inside equal-size groups.
            by_size = {}
            for path in self.files:
                try:
                    by_size.setdefault(path.stat().st_size, []).append(path)
                except OSError:
                    pass
                done += 1
                self.signals.progress.emit(done, total_steps, f"Размеры: {path.name}")

            exact_groups = []
            exact_members = set()
            candidates = [g for g in by_size.values() if len(g) > 1]
            for group in candidates:
                by_hash = {}
                for path in group:
                    try:
                        digest = file_sha256(path)
                        by_hash.setdefault(digest, []).append(path)
                    except Exception:
                        pass
                for items in by_hash.values():
                    if len(items) > 1:
                        exact_groups.append(items)
                        exact_members.update(str(p) for p in items)

            # Visual similarity for images via 64-bit dHash.
            images = [p for p in self.files if p.suffix.lower() in IMAGE_EXTENSIONS]
            hashes = []
            for path in images:
                try:
                    value = difference_hash(path)
                    if value is not None:
                        hashes.append((path, value))
                except Exception:
                    pass
                done += 1
                self.signals.progress.emit(min(done, total_steps), total_steps, f"Фото: {path.name}")

            parent = list(range(len(hashes)))

            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a, b):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            # Exact radius search in a BK-tree under Hamming distance. This avoids
            # quadratic all-pairs comparison on large photo libraries.
            tree = None

            def query_tree(node, value, radius, matches):
                if node is None:
                    return
                node_value, node_index, children = node
                distance = hamming_distance(value, node_value)
                if distance <= radius:
                    matches.append(node_index)
                low, high = distance - radius, distance + radius
                for edge, child in children.items():
                    if low <= edge <= high:
                        query_tree(child, value, radius, matches)

            def insert_tree(node, value, index):
                distance = hamming_distance(value, node[0])
                child = node[2].get(distance)
                if child is None:
                    node[2][distance] = [value, index, {}]
                else:
                    insert_tree(child, value, index)

            for i, (_path, value) in enumerate(hashes):
                if tree is None:
                    tree = [value, i, {}]
                    continue
                matches = []
                query_tree(tree, value, self.VISUAL_THRESHOLD, matches)
                for j in matches:
                    union(i, j)
                insert_tree(tree, value, i)

            groups = {}
            for i, (path, _hash) in enumerate(hashes):
                groups.setdefault(find(i), []).append(path)
            visual_groups = [items for items in groups.values() if len(items) > 1]
            visual_groups.sort(key=len, reverse=True)
            exact_groups.sort(key=len, reverse=True)
            self.signals.progress.emit(total_steps, total_steps, "Готово")
            self.signals.finished.emit(exact_groups, visual_groups)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class DuplicateDialog(QDialog):
    def __init__(self, files, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Дубликаты")
        self.resize(1050, 650)
        self.files = list(files)
        self.task = None

        self.info = QLabel(
            "Точные дубликаты сравниваются по SHA-256. Похожие фото — по perceptual dHash; "
            "это поиск визуально близких изображений, а не только одинаковых файлов."
        )
        self.info.setWordWrap(True)
        self.progress = QProgressBar()
        self.status = QLabel("Нажмите «Сканировать».")
        self.scan_btn = QPushButton("Сканировать")
        self.go_btn = QPushButton("Перейти к первому файлу группы")
        self.go_btn.setEnabled(False)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Тип", "Файлов", "Группа"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(lambda: self.go_btn.setEnabled(self.table.currentRow() >= 0))

        self.scan_btn.clicked.connect(self.start_scan)
        self.go_btn.clicked.connect(self.go_to_group)

        buttons = QHBoxLayout()
        buttons.addWidget(self.scan_btn)
        buttons.addWidget(self.go_btn)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self.info)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)

    def start_scan(self):
        if self.task is not None:
            return
        self.table.setRowCount(0)
        self.scan_btn.setEnabled(False)
        self.progress.setRange(0, max(1, len(self.files) * 2))
        self.progress.setValue(0)
        self.status.setText("Сканирование…")
        task = DuplicateScanTask(self.files)
        self.task = task
        task.signals.progress.connect(self.on_progress)
        task.signals.finished.connect(self.on_finished)
        task.signals.failed.connect(self.on_failed)
        task.signals.finished.connect(lambda *_args, t=task: None)
        QThreadPool.globalInstance().start(task)

    def on_progress(self, done, total, text):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, total))
        self.status.setText(text)

    def _add_group(self, kind, paths):
        row = self.table.rowCount()
        self.table.insertRow(row)
        type_item = QTableWidgetItem(kind)
        count_item = QTableWidgetItem(str(len(paths)))
        names_item = QTableWidgetItem("  |  ".join(p.name for p in paths))
        names_item.setToolTip("\n".join(str(p) for p in paths))
        names_item.setData(Qt.UserRole, str(paths[0]))
        self.table.setItem(row, 0, type_item)
        self.table.setItem(row, 1, count_item)
        self.table.setItem(row, 2, names_item)

    def on_finished(self, exact_groups, visual_groups):
        for group in exact_groups:
            self._add_group("Точный", group)
        for group in visual_groups:
            self._add_group("Похожие фото", group)
        self.status.setText(
            f"Готово: точных групп — {len(exact_groups)}, визуально похожих — {len(visual_groups)}."
        )
        self.scan_btn.setEnabled(True)
        self.task = None

    def on_failed(self, error):
        self.status.setText(f"Ошибка: {error}")
        self.scan_btn.setEnabled(True)
        self.task = None

    def go_to_group(self):
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 2)
        path = Path(item.data(Qt.UserRole)) if item else None
        parent = self.parent()
        if path and parent and hasattr(parent, "navigate_to_path"):
            parent.navigate_to_path(path)
            self.accept()


class MediaCategorizer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "media_categorizer/assets/app.ico")))
        self.resize(1400, 900)

        self.settings = load_settings()
        self.categories = normalize_categories(self.settings.get("categories"))
        self.files = []
        self.current_index = -1
        self.current_file = None
        self.current_image = QImage()
        self.undo_stack = []
        self.selected_tags = []
        self.category_buttons = {}
        self.category_actions = []
        self.fullscreen_mode = False
        self.thumbnail_cache = OrderedDict()
        self.pending_thumbnail_loads = set()
        self.ribbon_updating = False

        # Background copy/move queue: one active disk-heavy operation at a time.
        self.file_operation_queue = []
        self.active_file_operation = None
        self.file_reservations = FileReservations()

        self.image_cache = OrderedDict()
        self.image_cache_bytes = 0
        self.pending_image_loads = set()
        self.image_revisions = {}
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(max(2, min(4, self.thread_pool.maxThreadCount())))

        self.video_slots = [self._create_video_slot(i) for i in range(VIDEO_SLOT_COUNT)]
        self.active_video_slot = 0
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(self._refresh_audio_devices)
        self.timeline_dragging = False
        self._native_frame_guard = False

        apply_theme(self.settings.get("theme", "dark"), self.settings.get('ui_size', 'normal'), self.settings.get('icon_labels', False))
        self._build_ui()
        self._load_toggle_settings()
        self.build_category_buttons()
        self.create_global_shortcuts()
        self._update_last_folder_button()
        self.update_controls()
        self.refresh_theme()

        if bool(self.settings.get("window_maximized", True)):
            QTimer.singleShot(0, self, self.showMaximized)

    # ---------- UI ----------
    def _build_ui(self):
        self.progress_label = ElidedLabel("0 / 0 • осталось: 0")
        self.progress_label.setMinimumWidth(80)
        self.progress_label.setMaximumWidth(220)
        self.progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fullscreen_status_label = QLabel("0 / 0 • осталось: 0   •   F11 / Esc — выйти из полного экрана")
        self.fullscreen_status_label.setAlignment(Qt.AlignCenter)
        self.fullscreen_status_label.hide()

        brand = QLabel("Media Categorizer")
        brand.setObjectName("brand")
        self.open_folder_btn = IconButton("folder-open", "Открыть папку")
        self.open_folder_btn.setProperty("primary", True)
        self.last_folder_btn = IconButton("history", "Последняя папка", compact=True)
        self.open_file_btn = IconButton("file-plus", "Открыть файл", compact=True)
        self.undo_btn = IconButton("undo-2", "Отменить (Ctrl+Z)", compact=True)
        self.settings_btn = IconButton("settings-2", "Категории и действия")
        self.template_btn = IconButton("text-cursor-input", "Шаблон имени")
        self.log_btn = IconButton("scroll-text", "Журнал операций")
        self.duplicates_btn = IconButton("copy", "Поиск дубликатов")
        self.fullscreen_btn = IconButton("maximize", "Полный экран (F11)", compact=True)
        self.theme_btn = IconButton("sun", "Сменить тему", compact=True)
        self.theme_btn.icon_name = "moon" if QApplication.instance().property("theme") == "light" else "sun"
        self.theme_btn.refresh_icon()
        self.theme_btn.setToolTip('Выбрать тему оформления')
        theme_menu = QMenu(self.theme_btn)
        self.theme_actions = {}
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for theme_id, label in THEME_NAMES.items():
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(theme_id == QApplication.instance().property('theme'))
            action.triggered.connect(lambda checked=False, value=theme_id: self.set_theme(value))
            theme_group.addAction(action)
            self.theme_actions[theme_id] = action
        self.theme_btn.setMenu(theme_menu)
        self.menu_btn = IconButton("settings-2", "Настройки")
        menu = QMenu(self.menu_btn)
        for button, callback in ((self.settings_btn, self.edit_categories), (self.template_btn, self.edit_rename_template), (self.log_btn, self.show_log), (self.duplicates_btn, self.show_duplicates)):
            action = menu.addAction(button.text())
            action.triggered.connect(callback)
            button.clicked.connect(callback)
            button.setParent(self)
            button.hide()
        menu.addSeparator()
        menu.addAction('Размер интерфейса и подписи').triggered.connect(self.edit_appearance)
        menu.addAction("О программе и обновления").triggered.connect(self.show_updates)
        self.menu_btn.setMenu(menu)
        self.library_btn = IconButton("images", "Библиотека и пакетная обработка", compact=True)
        self.library_btn.clicked.connect(self.open_library)
        for button, label in ((self.last_folder_btn, 'Последняя папка'), (self.open_file_btn, 'Файл'), (self.library_btn, 'Библиотека'), (self.undo_btn, 'Отменить'), (self.theme_btn, 'Тема'), (self.fullscreen_btn, 'Полный экран')):
            button.setProperty('visibleLabel', label)
            button.refresh_size()
        self.open_folder_btn.clicked.connect(self.open_folder)
        self.last_folder_btn.clicked.connect(self.open_last_folder)
        self.open_file_btn.clicked.connect(self.open_file)
        self.undo_btn.clicked.connect(self.undo_last_action)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        self.open_folder_btn.setText('Открыть')
        self.open_folder_btn.clicked.disconnect(self.open_folder)
        open_menu = QMenu(self.open_folder_btn)
        for label,callback in (('Папку…',self.open_folder),('Файл…',self.open_file),('Последнюю папку',self.open_last_folder)):
            open_menu.addAction(label).triggered.connect(callback)
        self.open_folder_btn.setMenu(open_menu)
        for button in (self.open_file_btn,self.last_folder_btn,self.fullscreen_btn):
            button.setParent(self)
            button.hide()
        self.library_btn.compact = False
        self.library_btn.setMinimumWidth(0)
        self.library_btn.setMaximumWidth(16777215)
        self.library_btn.setText('Библиотека')
        self.theme_btn.setProperty('visibleLabel',None)
        self.theme_btn.refresh_size()
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0,0,0,0)
        brand.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        brand.setMinimumWidth(0)
        top_layout.addWidget(brand,1)
        for button in (self.open_folder_btn,self.library_btn,self.undo_btn):
            top_layout.addWidget(button)
        top_layout.addStretch()
        top_layout.addWidget(self.progress_label)
        for button in (self.menu_btn,self.theme_btn):
            top_layout.addWidget(button)
        self.top_widget = QWidget()
        self.top_widget.setLayout(top_layout)

        self.multi_btn = ToggleSwitch("Несколько тегов")
        self.apply_tags_btn = IconButton("check", "Применить теги")
        self.apply_tags_btn.setProperty("primary", True)
        self.loop_btn = ToggleSwitch("Повтор")
        self.sound_btn = ToggleSwitch("Звук")
        self.edit_photo_btn = IconButton("pencil", "Редактировать")
        self.edit_photo_btn.clicked.connect(self.edit_current_media)
        self.rotate_left_btn = IconButton("rotate-ccw", "Повернуть влево", compact=True)
        self.rotate_right_btn = IconButton("rotate-cw", "Повернуть вправо", compact=True)
        self.thumbnail_toggle_btn = IconButton("images", "Миниатюры", compact=True)
        self.thumbnail_toggle_btn.setCheckable(True)
        self.properties_toggle_btn = IconButton("info", "Параметры файла", compact=True)
        self.properties_toggle_btn.setCheckable(True)
        self.zoom_out_btn = IconButton("minus", "Уменьшить (Ctrl+−)", compact=True)
        self.zoom_in_btn = IconButton("plus", "Увеличить (Ctrl++)", compact=True)
        self.zoom_reset_btn = QPushButton("100%")
        self.zoom_reset_btn.setToolTip("Вписать в окно (Ctrl+0)")
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(25, 400)
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(25)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setFixedWidth(150)
        self.zoom_slider.setToolTip("Масштаб 25–400%. 100% автоматически вписывает файл в область просмотра")

        self.multi_btn.toggled.connect(self.set_multi_mode)
        self.apply_tags_btn.clicked.connect(self.apply_selected_tags)
        self.loop_btn.toggled.connect(self.set_loop_video)
        self.sound_btn.toggled.connect(self.set_sound_enabled)
        self.rotate_left_btn.clicked.connect(lambda: self.rotate_current_view(-90))
        self.rotate_right_btn.clicked.connect(lambda: self.rotate_current_view(90))
        self.thumbnail_toggle_btn.toggled.connect(self.set_thumbnail_ribbon_visible)
        self.properties_toggle_btn.toggled.connect(self.set_properties_visible)
        self.zoom_out_btn.clicked.connect(lambda: self.change_zoom(-10))
        self.zoom_in_btn.clicked.connect(lambda: self.change_zoom(10))
        self.zoom_reset_btn.clicked.connect(self.reset_zoom)
        self.zoom_slider.valueChanged.connect(self.set_zoom_percent)

        tools_layout = QHBoxLayout()
        tools_layout.setContentsMargins(0,0,0,0)
        tools_layout.addWidget(self.multi_btn)
        tools_layout.addWidget(self.apply_tags_btn)
        tools_layout.addStretch()
        tools_layout.addWidget(self.edit_photo_btn)
        self.view_menu_btn = IconButton('images','Просмотр')
        view_menu = QMenu(self.view_menu_btn)
        for label,callback in (('Вписать в окно',self.reset_zoom),('Увеличить',lambda:self.change_zoom(10)),('Уменьшить',lambda:self.change_zoom(-10)),('Повернуть влево',lambda:self.rotate_current_view(-90)),('Повернуть вправо',lambda:self.rotate_current_view(90)),('Полный экран',self.toggle_fullscreen)):
            view_menu.addAction(label).triggered.connect(callback)
        view_menu.addSeparator()
        for button in (self.thumbnail_toggle_btn,self.properties_toggle_btn):
            action = view_menu.addAction(button.toolTip())
            action.setCheckable(True)
            action.setChecked(button.isChecked())
            action.toggled.connect(button.setChecked)
            button.toggled.connect(action.setChecked)
        self.view_menu_btn.setMenu(view_menu)
        tools_layout.addWidget(self.view_menu_btn)
        for button in (self.rotate_left_btn,self.rotate_right_btn,self.thumbnail_toggle_btn,self.properties_toggle_btn):
            button.setParent(self)
            button.hide()
        self.tools_widget = QWidget()
        self.tools_widget.setLayout(tools_layout)

        # V4.5: the media viewport is now a real expanding layout container.
        # In V4.4 the canvas geometry was assigned manually; on some Windows/Qt
        # layouts that left the actual canvas close to its old minimum size
        # (about 400x260), even though the surrounding viewer looked much larger.
        # 100% zoom was therefore calculated from the wrong rectangle.
        self.media_stack = MediaViewport()
        self.media_stack.setMinimumSize(1, 1)
        self.media_stack.setContentsMargins(0, 0, 0, 0)
        self.media_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.media_pages = QStackedLayout(self.media_stack)
        self.media_pages.setContentsMargins(0, 0, 0, 0)
        self.media_pages.setSpacing(0)
        self.media_pages.setStackingMode(QStackedLayout.StackingMode.StackOne)

        self.image_label = ImageCanvas("Выберите папку с фото/видео")
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Video keeps a dedicated expanding host because VideoCanvas changes its
        # own geometry for zoom/cropping. The host itself is what the stacked
        # layout stretches to the full free viewer area.
        self.video_host = VideoViewport()
        self.video_host.setMinimumSize(1, 1)
        self.video_host.setContentsMargins(0, 0, 0, 0)
        self.video_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_canvas = VideoCanvas(self.video_host.viewport())
        self.image_label.zoomChanged.connect(self.set_zoom_percent)
        self.video_canvas.zoomChanged.connect(self.set_zoom_percent)
        self.video_canvas.videoSink().videoFrameChanged.connect(self._on_active_native_video_frame)
        self.video_host.resized.connect(self.video_canvas.fit_to_parent)

        self.media_pages.addWidget(self.image_label)
        self.media_pages.addWidget(self.video_host)
        self.media_pages.setCurrentWidget(self.image_label)
        self.video_canvas.hide()
        self.media_stack.resized.connect(self._on_media_viewport_resized)

        # Large navigation arrows live immediately beside the viewed media.
        self.left_nav_btn = IconButton("chevron-left", "Предыдущий файл (←)", compact=True)
        self.right_nav_btn = IconButton("chevron-right", "Следующий файл (→)", compact=True)
        self.left_nav_btn.setToolTip("Предыдущий файл (←)")
        self.right_nav_btn.setToolTip("Следующий файл (→)")
        self.left_nav_btn.clicked.connect(self.previous_file)
        self.right_nav_btn.clicked.connect(self.next_file)

        # Video transport controls: timeline and controls are centered directly
        # below the video instead of stretching from the left edge of the window.
        self.play_btn = IconButton("play", "Воспроизвести (Пробел)", compact=True)
        self.play_btn.setProperty("primary", True)
        self.play_btn.setFixedSize(46, 46)
        self.minus5_btn = IconButton("rotate-ccw", "−5 с")
        self.plus5_btn = IconButton("rotate-cw", "+5 с")
        self.frame_back_btn = IconButton("step-back", "Предыдущий кадр (Alt+←)", compact=True)
        self.frame_next_btn = IconButton("step-forward", "Следующий кадр (Alt+→)", compact=True)
        self.timeline = QSlider(Qt.Horizontal)
        self.timeline.setRange(0, 1000)
        self.timeline.setMinimumWidth(360)
        self.time_label = QLabel("00:00 / 00:00")
        self.speed_combo = QComboBox()
        for rate in (0.5, 1.0, 1.5, 2.0, 3.0):
            self.speed_combo.addItem(f"{rate:g}×", rate)

        self.play_btn.clicked.connect(self.toggle_play_pause)
        self.minus5_btn.clicked.connect(lambda: self.seek_relative(-5000))
        self.plus5_btn.clicked.connect(lambda: self.seek_relative(5000))
        self.frame_back_btn.clicked.connect(lambda: self.step_frame(-1))
        self.frame_next_btn.clicked.connect(lambda: self.step_frame(1))
        self.timeline.sliderPressed.connect(lambda: setattr(self, "timeline_dragging", True))
        self.timeline.sliderReleased.connect(self._timeline_released)
        self.timeline.sliderMoved.connect(self._timeline_moved)
        self.speed_combo.currentIndexChanged.connect(self.set_playback_rate_from_combo)

        timeline_row = QHBoxLayout()
        timeline_row.setContentsMargins(0, 0, 0, 0)
        timeline_row.addStretch()
        timeline_row.addWidget(self.timeline, 1)
        timeline_row.addWidget(self.time_label)
        timeline_row.addStretch()

        transport_row = QHBoxLayout()
        transport_row.setContentsMargins(0, 0, 0, 0)
        transport_row.addStretch()
        transport_row.addWidget(self.minus5_btn)
        transport_row.addWidget(self.frame_back_btn)
        transport_row.addWidget(self.play_btn)
        transport_row.addWidget(self.frame_next_btn)
        transport_row.addWidget(self.plus5_btn)
        transport_row.addSpacing(8)
        transport_row.addWidget(self.speed_combo)
        transport_row.addStretch()
        transport_row.addWidget(self.loop_btn)
        transport_row.addWidget(self.sound_btn)

        video_controls_layout = QVBoxLayout()
        video_controls_layout.setContentsMargins(0, 2, 0, 2)
        video_controls_layout.setSpacing(4)
        video_controls_layout.addLayout(timeline_row)
        video_controls_layout.addLayout(transport_row)
        self.video_controls_widget = QWidget()
        self.video_controls_widget.setMaximumWidth(900)
        self.video_controls_widget.setLayout(video_controls_layout)
        self.video_controls_widget.hide()

        viewer_row = QHBoxLayout()
        viewer_row.setContentsMargins(0, 0, 0, 0)
        viewer_row.addWidget(self.left_nav_btn)
        self.preview_host = PreviewHost(self.media_stack,(self.zoom_out_btn,self.zoom_slider,self.zoom_in_btn,self.zoom_reset_btn))
        self.preview_host.full_button.clicked.disconnect()
        self.preview_host.full_button.clicked.connect(self.toggle_fullscreen)
        self.video_canvas.pointerMoved.connect(self.preview_host.reveal)
        viewer_row.addWidget(self.preview_host, 1)
        viewer_row.addWidget(self.right_nav_btn)

        viewer_layout = QVBoxLayout()
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(4)
        viewer_layout.addLayout(viewer_row, 1)
        viewer_layout.addWidget(self.video_controls_widget, 0, Qt.AlignHCenter)
        self.viewer_widget = QWidget()
        self.viewer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.viewer_widget.setLayout(viewer_layout)

        # Technical properties panel. Hidden by default and opened on demand.
        self.properties_group = QGroupBox("Параметры файла")
        properties_form = QFormLayout(self.properties_group)
        self.property_labels = {}
        for key, title in (
            ("type", "Тип"),
            ("size", "Размер файла"),
            ("dimensions", "Разрешение"),
            ("duration", "Длительность"),
            ("fps", "FPS"),
            ("codec", "Кодек"),
            ("format", "Формат"),
            ("created", "Создан"),
            ("modified", "Изменён"),
        ):
            label = QLabel("—")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setWordWrap(True)
            self.property_labels[key] = label
            properties_form.addRow(title + ":", label)
        self.properties_group.setFixedWidth(265)
        self.properties_group.hide()

        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.addWidget(self.viewer_widget, 1)
        media_row.addWidget(self.properties_group)
        self.media_row_widget = QWidget()
        self.media_row_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.media_row_widget.setLayout(media_row)

        self.filename_label = ElidedLabel("")
        self.filename_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.filename_label.setWordWrap(False)
        self.tag_preview_label = QLabel("")
        self.tag_preview_label.setWordWrap(True)

        # Horizontal thumbnail ribbon. Hidden by default and opened on demand.
        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setViewMode(QListView.ViewMode.IconMode)
        self.thumbnail_list.setFlow(QListView.Flow.LeftToRight)
        self.thumbnail_list.setWrapping(False)
        self.thumbnail_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.thumbnail_list.setMovement(QListView.Movement.Static)
        self.thumbnail_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.thumbnail_list.setIconSize(QSize(140, 84))
        self.thumbnail_list.setGridSize(QSize(160, 116))
        self.thumbnail_list.setFixedHeight(128)
        self.thumbnail_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.thumbnail_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumbnail_list.itemClicked.connect(self.on_thumbnail_clicked)
        self.thumbnail_list.hide()

        self.category_widget = CategoryStrip()
        self.category_widget.reordered.connect(self.reorder_category)
        self.category_layout = QHBoxLayout(self.category_widget)
        self.category_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = CategoryScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(84)
        self.scroll.setWidget(self.category_widget)

        # Background copy/move progress is intentionally not shown in V4.2.
        # The queue still works and is still protected on application exit.
        self.background_label = QLabel("Фоновые операции: нет")
        self.background_progress = QProgressBar()
        self.background_progress.setRange(0, 100)
        self.background_progress.setValue(0)
        self.background_progress.setMaximumWidth(320)
        bg_layout = QHBoxLayout()
        bg_layout.addWidget(self.background_label)
        bg_layout.addWidget(self.background_progress)
        self.background_widget = QWidget()
        self.background_widget.setLayout(bg_layout)
        self.background_widget.hide()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(6)
        layout.addWidget(self.top_widget)
        layout.addWidget(self.tools_widget)
        layout.addWidget(self.fullscreen_status_label)
        layout.addWidget(self.media_row_widget, 1)
        layout.addWidget(self.filename_label)
        layout.addWidget(self.tag_preview_label)
        layout.addWidget(self.thumbnail_list)
        layout.addWidget(self.scroll)
        layout.addWidget(self.background_widget)
        self.setCentralWidget(central)

    def toggle_theme(self):
        self.set_theme("light" if QApplication.instance().property("theme") != "light" else "dark")

    def scroll_toolbar(self, content):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setWidget(content)
        area.setMinimumWidth(0)
        return area

    def refresh_theme(self):
        from media_categorizer.ui import ui_scale
        if hasattr(self, 'tools_widget'):
            for area in (self.top_widget, self.tools_widget):
                if isinstance(area, QScrollArea):
                    area.widget().layout().invalidate()
                    area.setFixedHeight(max(round(54*ui_scale()), area.widget().sizeHint().height()+round(16*ui_scale())))
        if isinstance(getattr(self, 'scroll', None), QScrollArea):
            self.scroll.setFixedHeight(round(84*ui_scale()))

    def edit_appearance(self):
        from media_categorizer.appearance import AppearanceDialog
        AppearanceDialog(self).exec()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.active_file_operation and not self.file_operation_queue:
            if any(url.isLocalFile() and (Path(url.toLocalFile()).is_dir() or Path(url.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS) for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        if self.active_file_operation or self.file_operation_queue:
            event.ignore()
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        try:
            files = self.dropped_files(paths)
            if files:
                self.open_library_selection(files, files[0], files[0].parent)
                event.acceptProposedAction()
            else:
                QMessageBox.information(self, APP_NAME, 'В выбранных файлах и папках нет поддерживаемых фото или видео.')
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, 'Не удалось открыть файлы: '+str(exc))

    @staticmethod
    def dropped_files(paths):
        from media_categorizer.file_operations import path_key
        result = {}
        for path in paths:
            candidates = sorted(path.iterdir(), key=lambda p: p.name.casefold()) if path.is_dir() else [path]
            for candidate in candidates:
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
                    result[path_key(candidate)] = candidate
        return list(result.values())

    def set_theme(self, theme):
        theme = theme if theme in COLORS else "dark"
        self.settings["theme"] = theme
        apply_theme(theme)
        self.theme_btn.icon_name = "moon" if theme == "light" else "sun"
        self.theme_btn.refresh_icon()
        self.theme_btn.setToolTip("Тема: " + THEME_NAMES[theme])
        for key, action in self.theme_actions.items():
            action.setChecked(key == theme)
        self._persist_settings()

    def _load_toggle_settings(self):
        self.multi_btn.setChecked(bool(self.settings.get("multi_tag_mode", False)))
        self.loop_btn.setChecked(bool(self.settings.get("loop_video", True)))

        # V4.5 keeps sound enabled by default. Old saved mute state from
        # V4.2 must not make the first opened clip unexpectedly silent.
        self.sound_btn.setChecked(True)
        self.settings["muted"] = False

        # V4.5 keeps video playback at the normal 1x startup speed.
        # An old persisted 0.5x value from V4 must not change startup behavior.
        idx = self.speed_combo.findData(1.0)
        self.speed_combo.blockSignals(True)
        self.speed_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.speed_combo.blockSignals(False)
        self.settings["playback_rate"] = 1.0

        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(100)
        self.zoom_slider.blockSignals(False)
        self.zoom_reset_btn.setText("100%")
        self.image_label.set_zoom_percent(100)
        self.video_canvas.set_zoom_percent(100)

        # Optional panels are closed by default on every launch.
        self.thumbnail_toggle_btn.setChecked(False)
        self.properties_toggle_btn.setChecked(False)
        self.thumbnail_list.hide()
        self.properties_group.hide()
        self._refresh_toggle_labels()

    # ---------- Settings/log ----------
    def _persist_settings(self):
        self.settings["categories"] = self.categories
        self.settings["multi_tag_mode"] = self.multi_btn.isChecked()
        self.settings["loop_video"] = self.loop_btn.isChecked()
        self.settings["muted"] = not self.sound_btn.isChecked()
        self.settings["playback_rate"] = 1.0
        self.settings.setdefault("rename_template", DEFAULT_TEMPLATE)
        save_settings(self.settings)

    def append_log(self, action, source, result="", status="OK", detail=""):
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "action": str(action),
            "source": str(source),
            "result": str(result),
            "status": str(status),
            "detail": str(detail),
        }
        try:
            with log_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def load_log_records(self, limit=500):
        path = log_path()
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
            records = []
            for line in lines:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        records.append(value)
                except Exception:
                    continue
            return records
        except Exception:
            return []

    def show_updates(self):
        from media_categorizer.updates import UpdatesDialog
        UpdatesDialog(self).exec()

    def open_library(self):
        if self.active_file_operation is not None or self.file_operation_queue:
            QMessageBox.information(self, APP_NAME, "Дождитесь завершения текущих операций.")
            return
        from media_categorizer.library import LibraryDialog
        LibraryDialog(self).exec()

    def open_library_selection(self, paths, selected, folder):
        self.stop_all_video()
        self.files = list(paths)
        self.current_index = self.files.index(selected)
        self._clear_image_cache()
        self.thumbnail_cache.clear()
        self.pending_thumbnail_loads.clear()
        self.undo_stack.clear()
        self.settings['last_folder'] = str(folder)
        self._persist_settings()
        self._update_last_folder_button()
        self.show_current_file()

    def refresh_after_batch(self, results):
        for result in results:
            if result.get('status') != 'OK':
                continue
            source, target = result['source'], result['target']
            if result['kind'] in ('rename', 'move'):
                self.files = [target if path == source else path for path in self.files]
            key = str(source)
            self.image_revisions[key] = self.image_revisions.get(key, 0) + 1
        self._clear_image_cache()
        self.thumbnail_cache.clear()
        self.pending_thumbnail_loads.clear()
        self.files = [path for path in self.files if path.exists()]
        self.current_index = min(self.current_index, max(0, len(self.files)-1))
        self.show_current_file()

    def show_log(self):
        LogDialog(self.load_log_records(), self).exec()

    def show_duplicates(self):
        if not self.files:
            QMessageBox.information(self, APP_NAME, "Сначала откройте папку с медиа.")
            return
        DuplicateDialog(self.files.copy(), self).exec()

    def navigate_to_path(self, path: Path):
        try:
            index = self.files.index(Path(path))
        except ValueError:
            return
        self.current_index = index
        self.show_current_file()

    def _refresh_toggle_labels(self):
        self.multi_btn.setText("Несколько тегов")
        self.loop_btn.setText("Повтор")
        self.sound_btn.setText("Звук")

    def _update_last_folder_button(self):
        last = str(self.settings.get("last_folder", ""))
        enabled = bool(last and Path(last).is_dir())
        self.last_folder_btn.setEnabled(enabled)
        self.last_folder_btn.setToolTip(last if enabled else "Последняя папка пока не сохранена")

    # ---------- Keyboard ----------
    def create_global_shortcuts(self):
        shortcuts = [
            (Qt.Key_Right, self.next_file),
            (Qt.Key_Left, self.previous_file),
            ("Ctrl+Z", self.undo_last_action),
            ("Space", self.toggle_play_pause),
            ("Ctrl+Left", lambda: self.seek_relative(-5000)),
            ("Ctrl+Right", lambda: self.seek_relative(5000)),
            ("Alt+Left", lambda: self.step_frame(-1)),
            ("Alt+Right", lambda: self.step_frame(1)),
            ("Ctrl++", lambda: self.change_zoom(10)),
            ("Ctrl+=", lambda: self.change_zoom(10)),
            ("Ctrl+-", lambda: self.change_zoom(-10)),
            ("Ctrl+0", self.reset_zoom),
            ("F11", self.toggle_fullscreen),
            ("Esc", self.exit_fullscreen),
        ]
        for key, callback in shortcuts:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(callback)
            self.addAction(action)

        enter_action = QAction(self)
        enter_action.setShortcuts([QKeySequence("Enter"), QKeySequence("Return")])
        enter_action.triggered.connect(self.apply_selected_tags)
        self.addAction(enter_action)

    def rebuild_category_shortcuts(self):
        for action in self.category_actions:
            self.removeAction(action)
            action.deleteLater()
        self.category_actions.clear()
        for item in self.categories:
            shortcut = item.get("shortcut", "")
            if not shortcut:
                continue
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(
                lambda checked=False, name=item["name"]: self.activate_category(name)
            )
            self.addAction(action)
            self.category_actions.append(action)

    # ---------- Fullscreen ----------
    def toggle_fullscreen(self):
        if self.fullscreen_mode:
            self.exit_fullscreen()
        else:
            self.fullscreen_mode = True
            self.top_widget.hide()
            self.tools_widget.hide()
            self.filename_label.hide()
            self.tag_preview_label.hide()
            self.fullscreen_status_label.show()
            self.showFullScreen()
            QTimer.singleShot(0, self, self._on_media_viewport_resized)

    def exit_fullscreen(self):
        if not self.fullscreen_mode:
            return
        self.fullscreen_mode = False
        self.top_widget.show()
        self.tools_widget.show()
        self.filename_label.show()
        self.tag_preview_label.show()
        self.fullscreen_status_label.hide()
        self.showMaximized()
        QTimer.singleShot(0, self, self._on_media_viewport_resized)

    def set_thumbnail_ribbon_visible(self, enabled):

        self.thumbnail_list.setVisible(bool(enabled))
        if enabled:
            self.refresh_thumbnail_ribbon()
        QTimer.singleShot(0, self, self._on_media_viewport_resized)

    def set_properties_visible(self, enabled):

        self.properties_group.setVisible(bool(enabled))
        if enabled:
            self.update_file_properties()
        QTimer.singleShot(0, self, self._on_media_viewport_resized)

    def edit_current_media(self):
        if self.current_file and self.current_file.suffix.lower() in VIDEO_EXTENSIONS:
            self.edit_video()
        else:
            self.edit_photo()

    def edit_video(self):
        path = self.current_file
        if not path or path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        operation_id = 'video-editor-' + uuid.uuid4().hex
        try:
            self.file_reservations.acquire(operation_id, path)
        except RuntimeError as exc:
            QMessageBox.information(self, APP_NAME, str(exc))
            return
        dialog = None
        try:
            self.update_controls()
            self.stop_all_video()
            dialog = VideoEditor(path, self)
            if dialog.exec() == QDialog.Accepted:
                self.thumbnail_cache.pop(str(path), None)
                options = dialog.options()
                self.append_log('EDIT_VIDEO', path, path, detail=f'Монтаж; {options.resolution} / {options.fps or "исходная частота"} fps; удалено фрагментов: {len(options.cuts)}; поворот: {options.rotation}°; отражение: {options.mirror}; кадр: {options.crop_ratio or "исходный"}; нормализация: {options.normalize}')
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, 'Редактор видео', str(exc))
        finally:
            if dialog is not None:
                dialog.deleteLater()
            self.file_reservations.release(operation_id)
            self.show_current_file()

    def edit_photo(self):
        path = self.current_file
        if not path or path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        operation_id = 'editor-' + uuid.uuid4().hex
        try:
            self.file_reservations.acquire(operation_id, path)
        except RuntimeError as exc:
            QMessageBox.information(self, APP_NAME, str(exc))
            return
        dialog = None
        try:
            self.update_controls()
            dialog = PhotoEditor(path, self)
            if dialog.exec() == QDialog.Accepted:
                key = str(path)
                self.image_revisions[key] = self.image_revisions.get(key, 0) + 1
                self._drop_cache_path(path)
                self.thumbnail_cache.pop(key, None)
                image = read_oriented_image(path)
                self._cache_image(path, image)
                self._cache_thumbnail(path, image)
                self.append_log('EDIT', path, path, detail='Правки фото / обрезка / поворот; исходное фото заменено')
                if self.current_file == path:
                    self.show_current_file()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Редактор фото', str(exc))
        finally:
            if dialog is not None:
                dialog.deleteLater()
            self.file_reservations.release(operation_id)
            self.update_controls()

    # ---------- Zoom ----------
    def set_zoom_percent(self, value):
        value = max(25, min(400, int(value)))
        if self.zoom_slider.value() != value:
            self.zoom_slider.blockSignals(True)
            self.zoom_slider.setValue(value)
            self.zoom_slider.blockSignals(False)
        self.zoom_reset_btn.setText(f"{value}%")
        self.image_label.set_zoom_percent(value)
        self.video_canvas.set_zoom_percent(value)
        if self.current_file and self.current_file.suffix.lower() in IMAGE_EXTENSIONS:
            self._display_current_image()

    def change_zoom(self, delta):
        self.set_zoom_percent(self.zoom_slider.value() + int(delta))

    def reset_zoom(self):
        self.set_zoom_percent(100)

    def _on_media_viewport_resized(self):
        # V4.5: QStackedLayout owns the image-page geometry. Do not manually
        # force ImageCanvas to an early/stale contentsRect; that was the source
        # of the tiny effective viewport seen in V4.3/V4.4.
        if self.current_file and self.current_file.suffix.lower() in IMAGE_EXTENSIONS:
            self.image_label.update()
        elif self.current_file and self.current_file.suffix.lower() in VIDEO_EXTENSIONS:
            # The dedicated video host is expanded by QStackedLayout first.
            # Queue the fit one event-loop turn later so it uses final geometry.
            QTimer.singleShot(0, self, self.video_canvas.fit_to_parent)

    # ---------- Categories ----------
    def build_category_buttons(self):
        while self.category_layout.count():
            item = self.category_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.category_buttons = {}

        for item in self.categories:
            name = item["name"]
            shortcut = item.get("shortcut", "")
            action_label = ACTION_LABELS.get(item.get("action", "rename"), "Переименовать")
            text = name
            if shortcut:
                text += f"  [{shortcut}]"
            button = CategoryButton(item, action_label)
            button.setToolTip(action_label + (f" → {item.get('destination')}" if item.get("destination") else ""))
            button.setMinimumHeight(56)
            button.setCheckable(self.multi_btn.isChecked())
            button.setChecked(name in self.selected_tags)
            button.clicked.connect(
                lambda checked=False, category=name: self.on_category_button(category, checked)
            )
            self.category_layout.addWidget(button)
            self.category_buttons[name] = button
        self.category_layout.addStretch()
        self.rebuild_category_shortcuts()

    def reorder_category(self, source, before):
        moving = next((item for item in self.categories if item['name'] == source), None)
        if moving is None or source == before:
            return
        self.categories.remove(moving)
        index = next((i for i, item in enumerate(self.categories) if item['name'] == before), len(self.categories))
        self.categories.insert(index, moving)
        self.build_category_buttons()
        self._persist_settings()

    def category_config(self, name):
        for item in self.categories:
            if item["name"] == name:
                return item
        return None

    def on_category_button(self, category, checked):
        if self.multi_btn.isChecked():
            self.set_tag_selected(category, checked)
        else:
            self.process_categories([category])

    def activate_category(self, category):
        if self.multi_btn.isChecked():
            selected = category not in self.selected_tags
            self.set_tag_selected(category, selected)
            button = self.category_buttons.get(category)
            if button:
                button.blockSignals(True)
                button.setChecked(selected)
                button.blockSignals(False)
        else:
            self.process_categories([category])

    def set_tag_selected(self, category, selected):
        if selected and category not in self.selected_tags:
            self.selected_tags.append(category)
        elif not selected and category in self.selected_tags:
            self.selected_tags.remove(category)
        self.update_tag_preview()
        self.update_controls()

    def clear_selected_tags(self):
        self.selected_tags.clear()
        for button in self.category_buttons.values():
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
        self.update_tag_preview()
        self.update_controls()

    def set_multi_mode(self, enabled):
        if not enabled:
            self.clear_selected_tags()
        self._refresh_toggle_labels()
        self.build_category_buttons()
        self.update_tag_preview()
        self.update_controls()
        self._persist_settings()

    def set_loop_video(self, enabled):
        self._refresh_toggle_labels()
        self._persist_settings()

    def set_sound_enabled(self, enabled):
        self._refresh_toggle_labels()
        self._sync_video_audio()
        self._persist_settings()

    def _refresh_audio_devices(self):
        device = QMediaDevices.defaultAudioOutput()
        for slot in self.video_slots:
            if slot['audio'].device() != device:
                slot['audio'].setDevice(device)
        self._sync_video_audio()

    def _sync_video_audio(self):
        if not hasattr(self, 'sound_btn'):
            return
        for index, slot in enumerate(self.video_slots):
            active = index == self.active_video_slot and slot['path'] == self.current_file and not slot['preloading'] and slot['path'] is not None
            player, audio = slot['player'], slot['audio']
            audio.setMuted(not active or not self.sound_btn.isChecked())
            audio.setVolume(1.)
            if active:
                if player.audioOutput() is not audio:
                    player.setAudioOutput(audio)
                if player.audioTracks() and player.activeAudioTrack() < 0:
                    player.setActiveAudioTrack(0)

    def edit_categories(self):
        dialog = CategoriesDialog(self.categories, self)
        if dialog.exec():
            self.categories = dialog.result_categories()
            self.settings["category_profiles"] = dialog.profiles
            self.settings["categories"] = self.categories
            self.clear_selected_tags()
            self.build_category_buttons()
            self._persist_settings()

    def edit_rename_template(self):
        dialog = RenameTemplateDialog(self.settings.get("rename_template", DEFAULT_TEMPLATE), self)
        if dialog.exec():
            self.settings["rename_template"] = dialog.result_template()
            self._persist_settings()
            self.update_tag_preview()

    # ---------- Files ----------
    def sanitize_category(self, category):
        return sanitize_category(category)

    def _folder_dialog_start(self):
        last = str(self.settings.get("last_folder", ""))
        return last if last and Path(last).is_dir() else str(Path.home())

    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку", self._folder_dialog_start())
        if folder:
            self.load_folder(Path(folder))

    def open_last_folder(self):
        last = str(self.settings.get("last_folder", ""))
        if last and Path(last).is_dir():
            self.load_folder(Path(last))

    def open_file(self):
        filters = (
            "Media files (*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff "
            "*.mp4 *.mov *.mkv *.avi *.wmv *.m4v *.webm *.mpeg *.mpg)"
        )
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл", self._folder_dialog_start(), filters
        )
        if filename:
            selected = Path(filename)
            self.load_folder(selected.parent, selected=selected)

    def load_folder(self, folder_path: Path, selected: Path | None = None):
        if self.active_file_operation is not None or self.file_operation_queue:
            QMessageBox.information(
                self, APP_NAME,
                "Сначала дождитесь завершения фонового копирования/перемещения, затем можно открыть другую папку."
            )
            return
        files = [
            p for p in folder_path.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        files.sort(key=lambda p: p.name.lower())
        if not files:
            QMessageBox.information(self, APP_NAME, "В этой папке нет поддерживаемых фото или видео.")
            return

        self.stop_all_video()
        self.files = files
        self._clear_image_cache()
        self.thumbnail_cache.clear()
        self.pending_thumbnail_loads.clear()
        self.undo_stack.clear()
        self.clear_selected_tags()
        if selected is not None:
            try:
                self.current_index = self.files.index(selected)
            except ValueError:
                self.current_index = 0
        else:
            self.current_index = 0
        self.settings["last_folder"] = str(folder_path)
        self._persist_settings()
        self._update_last_folder_button()
        self.show_current_file()

    # ---------- Image cache / preload ----------
    def _image_bytes(self, image: QImage) -> int:
        try:
            return int(image.sizeInBytes())
        except Exception:
            return max(0, image.width() * image.height() * 4)

    def _clear_image_cache(self):
        self.image_cache.clear()
        self.image_cache_bytes = 0
        self.pending_image_loads.clear()

    def _cache_image(self, path: Path, image: QImage):
        if image.isNull():
            return
        key = str(path)
        if key in self.image_cache:
            old = self.image_cache.pop(key)
            self.image_cache_bytes -= self._image_bytes(old)
        self.image_cache[key] = image
        self.image_cache.move_to_end(key)
        self.image_cache_bytes += self._image_bytes(image)
        while (
            len(self.image_cache) > IMAGE_CACHE_MAX_ITEMS
            or self.image_cache_bytes > IMAGE_CACHE_MAX_BYTES
        ) and self.image_cache:
            _, evicted = self.image_cache.popitem(last=False)
            self.image_cache_bytes -= self._image_bytes(evicted)

    def _take_cached_image(self, path: Path) -> QImage:
        key = str(path)
        image = self.image_cache.get(key)
        if image is None:
            return QImage()
        self.image_cache.move_to_end(key)
        return image

    def _request_image_preload(self, path: Path):
        key = str(path)
        if key in self.image_cache or key in self.pending_image_loads or not path.exists():
            return
        self.pending_image_loads.add(key)
        task = ImageLoadTask(path, self.image_revisions.get(key, 0))
        task.signals.loaded.connect(self._on_image_preloaded)
        # Keep task/signals alive until the queued signal is delivered.
        task.signals.loaded.connect(lambda _p, _i, _r, t=task: None)
        self.thread_pool.start(task)

    def _on_image_preloaded(self, path_str, image, revision=0):
        self.pending_image_loads.discard(path_str)
        if revision != self.image_revisions.get(path_str, 0):
            return
        path = Path(path_str)
        if not image.isNull() and path.exists():
            self._cache_image(path, image)

    def _preload_window(self):
        if not self.files or self.current_index < 0:
            return

        # Images around current file.
        start = max(0, self.current_index - IMAGE_PRELOAD_BACKWARD)
        end = min(len(self.files), self.current_index + IMAGE_PRELOAD_FORWARD + 1)
        for idx in range(start, end):
            if idx == self.current_index:
                continue
            path = self.files[idx]
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                self._request_image_preload(path)

    def _preload_video_window(self):
        if not self.files or self.current_index < 0:
            return

        # High-resolution/high-frame-rate video should get the decoder/GPU to
        # itself. Preloading another video in parallel can make 4K/60fps clips
        # stutter on otherwise capable PCs.
        if self.current_file and self.current_file.suffix.lower() in VIDEO_EXTENSIONS:
            slot = self.video_slots[self.active_video_slot]
            size = slot.get("frame_size", QSize())
            pixels = size.width() * size.height() if size.isValid() else 0
            fps = 1000.0 / max(1.0, float(slot.get("frame_ms", 33.333)))
            if pixels >= 3_000_000 or fps >= 50.0:
                self._ensure_video_preloaded([])
                return

        upcoming = []
        scan_end = min(len(self.files), self.current_index + VIDEO_SCAN_AHEAD + 1)
        for idx in range(self.current_index + 1, scan_end):
            path = self.files[idx]
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.exists():
                upcoming.append(path)
                if len(upcoming) >= VIDEO_PRELOAD_COUNT:
                    break
        self._ensure_video_preloaded(upcoming)


    # ---------- Thumbnail ribbon ----------
    def _video_placeholder_icon(self, path: Path) -> QIcon:
        pix = QPixmap(140, 84)
        pix.fill(Qt.black)
        painter = QPainter(pix)
        painter.setPen(Qt.white)
        painter.setBrush(Qt.white)
        cx, cy = 70, 38
        points = [(cx - 12, cy - 18), (cx - 12, cy + 18), (cx + 20, cy)]
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        painter.drawText(6, 78, path.suffix.upper().lstrip('.'))
        painter.end()
        return QIcon(pix)

    def _cache_thumbnail(self, path: Path, image: QImage):
        if image.isNull():
            return
        key = str(path)
        thumb = image.scaled(140, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.thumbnail_cache[key] = thumb
        self.thumbnail_cache.move_to_end(key)
        while len(self.thumbnail_cache) > 200:
            self.thumbnail_cache.popitem(last=False)
        self._update_thumbnail_for_path(path, thumb)

    def _request_thumbnail(self, path: Path):
        key = str(path)
        if key in self.thumbnail_cache or key in self.pending_thumbnail_loads:
            return
        if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.exists():
            return
        self.pending_thumbnail_loads.add(key)
        task = ThumbnailTask(path, revision=self.image_revisions.get(key, 0))
        task.signals.loaded.connect(self._on_thumbnail_loaded)
        task.signals.loaded.connect(lambda _p, _i, _r, t=task: None)
        self.thread_pool.start(task)

    def _on_thumbnail_loaded(self, path_str, image, revision=0):
        self.pending_thumbnail_loads.discard(path_str)
        if revision != self.image_revisions.get(path_str, 0):
            return
        if not image.isNull():
            self._cache_thumbnail(Path(path_str), image)

    def _update_thumbnail_for_path(self, path: Path, image: QImage):
        key = str(path)
        if not image.isNull():
            self.thumbnail_cache[key] = image.scaled(140, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        for i in range(self.thumbnail_list.count()):
            item = self.thumbnail_list.item(i)
            if item.data(Qt.UserRole + 1) == key:
                cached = self.thumbnail_cache.get(key)
                if cached is not None and not cached.isNull():
                    item.setIcon(QIcon(QPixmap.fromImage(cached)))

    def refresh_thumbnail_ribbon(self):
        self.ribbon_updating = True
        self.thumbnail_list.clear()
        if not self.thumbnail_toggle_btn.isChecked():
            self.ribbon_updating = False
            return
        if not self.files or self.current_index < 0:
            self.ribbon_updating = False
            return
        start = max(0, self.current_index - 12)
        end = min(len(self.files), self.current_index + 13)
        selected_item = None
        for index in range(start, end):
            path = self.files[index]
            item = QListWidgetItem(path.name)
            item.setToolTip(str(path))
            item.setData(Qt.UserRole, index)
            item.setData(Qt.UserRole + 1, str(path))
            cached = self.thumbnail_cache.get(str(path))
            if cached is not None and not cached.isNull():
                item.setIcon(QIcon(QPixmap.fromImage(cached)))
            elif path.suffix.lower() in VIDEO_EXTENSIONS:
                item.setIcon(self._video_placeholder_icon(path))
            else:
                self._request_thumbnail(path)
            self.thumbnail_list.addItem(item)
            if index == self.current_index:
                selected_item = item
        if selected_item is not None:
            selected_item.setSelected(True)
            self.thumbnail_list.setCurrentItem(selected_item)
            self.thumbnail_list.scrollToItem(selected_item, QAbstractItemView.ScrollHint.PositionAtCenter)
        self.ribbon_updating = False

    def on_thumbnail_clicked(self, item):
        if self.ribbon_updating or item is None:
            return
        index = item.data(Qt.UserRole)
        if isinstance(index, int) and 0 <= index < len(self.files):
            self.current_index = index
            self.show_current_file()

    # ---------- Video ----------
    def _create_video_slot(self, index):
        player = QMediaPlayer(self)
        audio = QAudioOutput(self)
        audio.setVolume(1.)
        audio.setMuted(True)
        player.setAudioOutput(audio)
        sink = QVideoSink(self)
        player.setVideoOutput(sink)
        slot = {
            "player": player,
            "audio": audio,
            "sink": sink,
            "path": None,
            "preloading": False,
            "preview": QImage(),
            "frame_ms": 33.333,
            "last_frame_start_us": None,
            "metadata_rotation": None,
            "frame_size": QSize(),
            "observed_rotation": 0,
            "display_rotation": 0,
        }
        sink.videoFrameChanged.connect(lambda frame, i=index: self._on_video_frame(i, frame))
        player.mediaStatusChanged.connect(lambda status, i=index: self._on_media_status(i, status))
        player.tracksChanged.connect(self._sync_video_audio)
        player.positionChanged.connect(lambda pos, i=index: self._on_position_changed(i, pos))
        player.durationChanged.connect(lambda dur, i=index: self._on_duration_changed(i, dur))
        try:
            player.metaDataChanged.connect(lambda i=index: self._on_metadata_changed(i))
        except Exception:
            pass
        player.playbackStateChanged.connect(lambda state, i=index: self._on_playback_state_changed(i, state))
        player.errorOccurred.connect(lambda error, error_string, i=index: self._on_media_error(i, error, error_string))
        return slot

    def _stop_slot(self, index, clear_source=True):
        slot = self.video_slots[index]
        try:
            slot["player"].stop()
            if clear_source:
                slot["player"].setSource(QUrl())
        except Exception:
            pass
        try:
            slot["player"].setVideoOutput(slot["sink"])
        except Exception:
            pass
        slot["path"] = None
        slot["preloading"] = False
        slot["preview"] = QImage()
        slot["last_frame_start_us"] = None
        slot["metadata_rotation"] = None
        slot["frame_size"] = QSize()
        slot["observed_rotation"] = 0
        slot["display_rotation"] = 0
        slot["audio"].setMuted(True)

    def stop_all_video(self):
        for i in range(len(self.video_slots)):
            self._stop_slot(i)
        self.video_canvas.clear()
        self._reset_timeline()

    def stop_active_video(self):
        self._stop_slot(self.active_video_slot)
        self.video_canvas.clear()
        self._reset_timeline()

    def _find_video_slot(self, path: Path):
        for index, slot in enumerate(self.video_slots):
            if slot["path"] == path:
                return index
        return None

    def _free_video_slot(self, protected_paths=None):
        protected_paths = set(protected_paths or [])
        for i, slot in enumerate(self.video_slots):
            if i != self.active_video_slot and slot["path"] is None:
                return i
        for i, slot in enumerate(self.video_slots):
            if i != self.active_video_slot and slot["path"] not in protected_paths:
                self._stop_slot(i)
                return i
        return None

    def _start_video_fresh(self, path: Path):
        # Active playback goes straight to QVideoWidget.  This avoids the old
        # per-frame QVideoFrame -> QImage -> QPainter conversion path.
        self._stop_slot(self.active_video_slot)
        slot = self.video_slots[self.active_video_slot]
        slot["path"] = path
        slot["preloading"] = False
        slot["preview"] = QImage()
        slot["audio"].setMuted(not self.sound_btn.isChecked())
        slot["player"].setPlaybackRate(float(self.speed_combo.currentData() or 1.0))
        slot["player"].setVideoOutput(self.video_canvas)
        slot["player"].setSource(QUrl.fromLocalFile(str(path)))
        self._sync_video_audio()
        slot["player"].play()

    def _activate_preloaded_video(self, index, path: Path):
        old_active = self.active_video_slot
        if old_active != index:
            self._stop_slot(old_active)
        self.active_video_slot = index
        slot = self.video_slots[index]
        slot["preloading"] = False
        slot["audio"].setMuted(not self.sound_btn.isChecked())
        slot["player"].setPlaybackRate(float(self.speed_combo.currentData() or 1.0))
        slot["player"].setVideoOutput(self.video_canvas)
        # A preloader has already consumed media up to its first decoded frame.
        # Reattach audio and rewind before playback, including very short clips.
        slot["player"].setAudioOutput(slot['audio'])
        slot["player"].setPosition(0)
        self._sync_video_audio()
        self._refresh_slot_metadata_rotation(index)
        slot["player"].play()
        self._on_duration_changed(index, slot["player"].duration())
        self._on_position_changed(index, slot["player"].position())

    def _preload_video(self, path: Path, protected_paths):
        existing = self._find_video_slot(path)
        if existing is not None:
            return
        index = self._free_video_slot(protected_paths)
        if index is None:
            return
        slot = self.video_slots[index]
        slot["path"] = path
        slot["preloading"] = True
        slot["preview"] = QImage()
        slot["audio"].setMuted(True)
        slot["player"].setAudioOutput(None)
        slot["player"].setVideoOutput(slot["sink"])
        slot["player"].setSource(QUrl.fromLocalFile(str(path)))
        slot["player"].play()

    def _ensure_video_preloaded(self, paths):
        protected = set(paths)
        if self.current_file and self.current_file.suffix.lower() in VIDEO_EXTENSIONS:
            protected.add(self.current_file)

        # Drop stale inactive preloaders.
        for i, slot in enumerate(self.video_slots):
            if i == self.active_video_slot:
                continue
            if slot["path"] is not None and slot["path"] not in protected:
                self._stop_slot(i)
        for path in paths:
            self._preload_video(path, protected)

    def _frame_rotations(self, frame):
        surface_rotation = 0
        frame_rotation = 0
        try:
            fmt = frame.surfaceFormat()
            if hasattr(fmt, "rotation"):
                surface_rotation = rotation_degrees(fmt.rotation())
        except Exception:
            pass
        try:
            if hasattr(frame, "rotation"):
                frame_rotation = rotation_degrees(frame.rotation())
            elif hasattr(frame, "rotationAngle"):
                frame_rotation = rotation_degrees(frame.rotationAngle())
        except Exception:
            pass
        return surface_rotation % 360, frame_rotation % 360

    def _refresh_slot_metadata_rotation(self, index):
        slot = self.video_slots[index]
        try:
            value = self._metadata_value(slot["player"].metaData(), "Orientation")
            if value is None:
                slot["metadata_rotation"] = None
            else:
                slot["metadata_rotation"] = int(value) % 360
        except Exception:
            slot["metadata_rotation"] = None

    def _corrected_rotation_for_frame(self, slot, frame):
        surface_rotation, frame_rotation = self._frame_rotations(frame)
        observed_total = (surface_rotation + frame_rotation) % 360
        metadata_rotation = slot.get("metadata_rotation")

        # Qt normally exposes container rotation through the frame/surface.
        # Some phone files expose it only through QMediaMetaData::Orientation;
        # use that as a fallback only when the frame itself says "0°" to avoid
        # double-rotating correctly tagged videos.
        base_total = observed_total
        if observed_total == 0 and metadata_rotation in (90, 180, 270):
            base_total = metadata_rotation

        desired_total = (base_total + self.video_canvas.manual_rotation) % 360
        desired_frame_rotation = (desired_total - surface_rotation) % 360
        return surface_rotation, frame_rotation, observed_total, desired_total, desired_frame_rotation

    def _on_active_native_video_frame(self, frame):
        if self._native_frame_guard or not frame or not frame.isValid():
            return
        if not self.current_file or self.current_file.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        slot = self.video_slots[self.active_video_slot]
        if slot.get("path") != self.current_file or slot.get("preloading"):
            return

        size = frame.size()
        if size.isValid():
            slot["frame_size"] = QSize(size)

        # Frame duration is needed only for one-frame stepping. Avoid doing
        # timestamp arithmetic and image conversion on every decoded frame.
        try:
            rate = float(frame.streamFrameRate())
            if rate > 1.0:
                slot["frame_ms"] = 1000.0 / rate
        except Exception:
            pass

        surface_rotation, frame_rotation, observed_total, desired_total, desired_frame_rotation = \
            self._corrected_rotation_for_frame(slot, frame)
        slot["observed_rotation"] = observed_total
        slot["display_rotation"] = desired_total
        self.video_canvas.set_video_geometry(size, desired_total - self.video_canvas.manual_rotation)

        # If a mobile video keeps rotation only in metadata, or the user pressed
        # one of the manual View buttons, alter presentation metadata on a shared
        # QVideoFrame copy. Pixel data stays on the native Qt video path.
        if desired_frame_rotation != frame_rotation:
            try:
                corrected = QVideoFrame(frame)
                if set_video_frame_rotation(corrected, desired_frame_rotation):
                    self._native_frame_guard = True
                    try:
                        self.video_canvas.videoSink().setVideoFrame(corrected)
                    finally:
                        self._native_frame_guard = False
            except Exception:
                pass

        # Generate at most one thumbnail image. The old V4.1 converted every
        # frame to QImage; on 4K/60fps media that was the main CPU bottleneck.
        if str(slot["path"]) not in self.thumbnail_cache:
            try:
                thumb_frame = QVideoFrame(frame)
                if desired_frame_rotation != frame_rotation:
                    set_video_frame_rotation(thumb_frame, desired_frame_rotation)
                image = oriented_video_frame_image(thumb_frame)
                if not image.isNull():
                    self._cache_thumbnail(slot["path"], image)
            except Exception:
                pass

    def _on_video_frame(self, index, frame):
        """Frames from inactive preloader slots only."""
        slot = self.video_slots[index]
        if slot["path"] is None or not slot.get("preloading"):
            return

        try:
            rate = float(frame.streamFrameRate())
            if rate > 1.0:
                slot["frame_ms"] = 1000.0 / rate
        except Exception:
            pass

        if slot["preview"].isNull():
            self._refresh_slot_metadata_rotation(index)
            image = oriented_video_frame_image(frame)
            # Metadata-orientation fallback for preload thumbnails as well.
            if not image.isNull():
                surface_rotation, frame_rotation = self._frame_rotations(frame)
                if (surface_rotation + frame_rotation) % 360 == 0 and slot.get("metadata_rotation") in (90, 180, 270):
                    image = rotate_and_mirror(image, slot["metadata_rotation"], False)
                slot["preview"] = image
                slot["frame_size"] = frame.size()
                self._cache_thumbnail(slot["path"], image)
                slot["player"].pause()

    def _on_media_status(self, index, status):
        if index != self.active_video_slot:
            return
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self._sync_video_audio()
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.loop_btn.isChecked():
            slot = self.video_slots[index]
            if slot["path"] == self.current_file:
                slot["player"].setPosition(0)
                slot["player"].play()

    def _on_position_changed(self, index, position):
        if index != self.active_video_slot:
            return
        slot = self.video_slots[index]
        if slot["path"] != self.current_file or slot["preloading"]:
            return
        duration = max(0, int(slot["player"].duration()))
        if duration > 0 and not self.timeline_dragging:
            value = int(max(0, min(1000, position * 1000 / duration)))
            self.timeline.blockSignals(True)
            self.timeline.setValue(value)
            self.timeline.blockSignals(False)
        self.time_label.setText(f"{format_millis(position)} / {format_millis(duration)}")

    def _on_duration_changed(self, index, duration):
        if index == self.active_video_slot:
            self._on_position_changed(index, self.video_slots[index]["player"].position())
            self.update_file_properties()

    def _on_playback_state_changed(self, index, state):
        if index != self.active_video_slot:
            return
        if state == QMediaPlayer.PlayingState:
            self._sync_video_audio()
        self.play_btn.set_playing(state == QMediaPlayer.PlaybackState.PlayingState)

    def _on_media_error(self, index, error, error_string):
        slot = self.video_slots[index]
        if slot.get("path") is None:
            return
        # Preload errors are logged silently; active-file errors are also visible in the log.
        detail = str(error_string or error)
        self.append_log("VIDEO_OPEN", slot["path"], status="ERROR", detail=detail)

    def _active_player(self):
        if not self.current_file or self.current_file.suffix.lower() not in VIDEO_EXTENSIONS:
            return None
        slot = self.video_slots[self.active_video_slot]
        if slot["path"] != self.current_file or slot["preloading"]:
            return None
        return slot["player"]

    def toggle_play_pause(self):
        player = self._active_player()
        if player is None:
            return
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()

    def seek_relative(self, delta_ms):
        player = self._active_player()
        if player is None:
            return
        target = max(0, min(player.duration(), player.position() + int(delta_ms)))
        player.setPosition(target)

    def step_frame(self, direction):
        player = self._active_player()
        if player is None:
            return
        slot = self.video_slots[self.active_video_slot]
        player.pause()
        frame_ms = max(1, int(round(slot.get("frame_ms", 33.333))))
        target = max(0, min(player.duration(), player.position() + direction * frame_ms))
        player.setPosition(target)

    def _timeline_moved(self, value):
        player = self._active_player()
        if player is None or player.duration() <= 0:
            return
        position = int(player.duration() * value / 1000)
        self.time_label.setText(f"{format_millis(position)} / {format_millis(player.duration())}")

    def _timeline_released(self):
        player = self._active_player()
        if player is not None and player.duration() > 0:
            player.setPosition(int(player.duration() * self.timeline.value() / 1000))
        self.timeline_dragging = False

    def set_playback_rate_from_combo(self):
        rate = float(self.speed_combo.currentData() or 1.0)
        player = self._active_player()
        if player is not None:
            player.setPlaybackRate(rate)

    def _reset_timeline(self):
        self.timeline.blockSignals(True)
        self.timeline.setValue(0)
        self.timeline.blockSignals(False)
        self.time_label.setText("00:00 / 00:00")
        self.play_btn.set_playing(False)


    def _metadata_value(self, metadata, key_name):
        try:
            key = getattr(QMediaMetaData.Key, key_name)
            return metadata.value(key)
        except Exception:
            return None

    def _on_metadata_changed(self, index):
        self._refresh_slot_metadata_rotation(index)
        if index == self.active_video_slot:
            # A few phone containers publish rotation only after metadata is
            # parsed. Re-present the current frame with that correction.
            try:
                frame = self.video_canvas.videoSink().videoFrame()
                if frame.isValid():
                    self._on_active_native_video_frame(frame)
            except Exception:
                pass
            self.update_file_properties()

    def update_file_properties(self):
        for label in self.property_labels.values():
            label.setText("—")
        path = self.current_file
        if not path or not path.exists():
            return
        try:
            stat = path.stat()
            self.property_labels["size"].setText(human_size(stat.st_size))
            self.property_labels["created"].setText(datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"))
            self.property_labels["modified"].setText(datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass

        ext = path.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            self.property_labels["type"].setText("Фото")
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            size = self.current_image.size() if not self.current_image.isNull() else reader.size()
            if size.isValid():
                self.property_labels["dimensions"].setText(f"{size.width()} × {size.height()}")
            try:
                fmt = bytes(reader.format()).decode("ascii", errors="ignore").upper()
                self.property_labels["format"].setText(fmt or path.suffix.upper().lstrip('.'))
            except Exception:
                self.property_labels["format"].setText(path.suffix.upper().lstrip('.'))
        elif ext in VIDEO_EXTENSIONS:
            self.property_labels["type"].setText("Видео")
            self.property_labels["format"].setText(path.suffix.upper().lstrip('.'))
            slot = self.video_slots[self.active_video_slot]
            if slot.get("path") == path:
                player = slot["player"]
                duration = int(player.duration() or 0)
                if duration:
                    self.property_labels["duration"].setText(format_millis(duration))
                try:
                    metadata = player.metaData()
                    resolution = self._metadata_value(metadata, "Resolution")
                    if resolution is not None and hasattr(resolution, "width"):
                        self.property_labels["dimensions"].setText(f"{resolution.width()} × {resolution.height()}")
                    fps = self._metadata_value(metadata, "VideoFrameRate")
                    if fps:
                        self.property_labels["fps"].setText(f"{float(fps):.3g}")
                    codec = self._metadata_value(metadata, "VideoCodec")
                    if codec is not None:
                        name = getattr(codec, "name", None) or str(codec)
                        self.property_labels["codec"].setText(str(name))
                    file_format = self._metadata_value(metadata, "FileFormat")
                    if file_format is not None:
                        name = getattr(file_format, "name", None) or str(file_format)
                        self.property_labels["format"].setText(str(name))
                except Exception:
                    pass

    # ---------- Display ----------
    def show_current_file(self):
        self.clear_selected_tags()
        self.image_label.reset_pan()
        self.video_canvas.reset_pan()
        self.video_canvas.reset_manual_rotation()

        while self.files and 0 <= self.current_index < len(self.files):
            if self.files[self.current_index].exists():
                break
            self.files.pop(self.current_index)

        if not self.files or self.current_index < 0:
            self.current_file = None
            self.current_image = QImage()
            self.stop_all_video()
            self.image_label.clear()
            self.image_label.setText("Нет файлов для отображения.")
            self.media_pages.setCurrentWidget(self.image_label)
            self.image_label.show()
            self.video_canvas.hide()
            self.video_controls_widget.hide()
            self.filename_label.setText("")
            self.refresh_thumbnail_ribbon()
            self.update_file_properties()
            self.update_progress()
            self.update_controls()
            return

        if self.current_index >= len(self.files):
            self.current_file = None
            self.current_image = QImage()
            self.stop_all_video()
            self.image_label.clear()
            self.image_label.setText("Готово: файлы закончились.")
            self.media_pages.setCurrentWidget(self.image_label)
            self.image_label.show()
            self.video_canvas.hide()
            self.video_controls_widget.hide()
            self.filename_label.setText("")
            self.refresh_thumbnail_ribbon()
            self.update_file_properties()
            self.update_progress(done=True)
            self.update_controls()
            return

        path = self.files[self.current_index]
        self.current_file = path
        self.filename_label.setText(str(path))
        self.update_progress()
        ext = path.suffix.lower()

        if ext in IMAGE_EXTENSIONS:
            self.stop_active_video()
            self.media_pages.setCurrentWidget(self.image_label)
            self.video_canvas.hide()
            self.video_controls_widget.hide()
            self.image_label.show()
            image = self._take_cached_image(path)
            if image.isNull():
                image = read_oriented_image(path)
                if not image.isNull():
                    self._cache_image(path, image)
            self.current_image = image
            if image.isNull():
                self.image_label.clear()
                self.image_label.setText("Не удалось открыть изображение.")
                self.append_log("OPEN", path, status="ERROR", detail="Не удалось декодировать изображение")
            else:
                self._display_current_image()

        elif ext in VIDEO_EXTENSIONS:
            self.current_image = QImage()
            self.media_pages.setCurrentWidget(self.video_host)
            self.image_label.hide()
            self.video_canvas.show()
            self.video_controls_widget.show()
            preloaded_index = self._find_video_slot(path)
            if preloaded_index is not None and preloaded_index != self.active_video_slot:
                self._activate_preloaded_video(preloaded_index, path)
            elif preloaded_index == self.active_video_slot:
                slot = self.video_slots[self.active_video_slot]
                slot["preloading"] = False
                slot["audio"].setMuted(not self.sound_btn.isChecked())
                slot["player"].setVideoOutput(self.video_canvas)
                self._refresh_slot_metadata_rotation(self.active_video_slot)
                slot["player"].play()
            else:
                self._start_video_fresh(path)

        self.update_tag_preview()
        self.refresh_thumbnail_ribbon()
        self.update_file_properties()
        self.update_controls()
        QTimer.singleShot(0, self, self._on_media_viewport_resized)
        QTimer.singleShot(0, self, self._preload_window)
        QTimer.singleShot(900, self, self._preload_video_window)

    def _display_current_image(self):
        if self.current_image.isNull():
            return
        # ImageCanvas calculates the fitted rectangle from its CURRENT viewport
        # on every paint. This fixes the old QLabel/pixmap geometry issue where
        # the image could stay much smaller than the visibly free media area.
        self.image_label.set_zoom_percent(self.zoom_slider.value())
        self.image_label.set_image(self.current_image)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self._on_media_viewport_resized)

    def rotate_current_view(self, degrees):
        if not self.current_file:
            return
        if self.current_file.suffix.lower() in IMAGE_EXTENSIONS:
            if not self.current_image.isNull():
                transform = QTransform()
                transform.rotate(degrees)
                self.current_image = self.current_image.transformed(transform, Qt.SmoothTransformation)
                self._display_current_image()
        else:
            self.video_canvas.rotate_view(degrees)
            try:
                frame = self.video_canvas.videoSink().videoFrame()
                if frame.isValid():
                    self._on_active_native_video_frame(frame)
            except Exception:
                pass

    def update_progress(self, done=False):
        total = len(self.files)
        if done:
            text = f"{total} / {total} • осталось: 0"
            self.progress_label.setText(text)
            self.fullscreen_status_label.setText(text + "   •   F11 / Esc — выйти из полного экрана")
            return
        if not total or self.current_index < 0:
            text = "0 / 0 • осталось: 0"
            self.progress_label.setText(text)
            self.fullscreen_status_label.setText(text + "   •   F11 / Esc — выйти из полного экрана")
            return
        shown = min(self.current_index + 1, total)
        remaining = max(total - shown, 0)
        text = f"{shown} / {total} • после текущего: {remaining}"
        self.progress_label.setText(text)
        self.fullscreen_status_label.setText(text + "   •   F11 / Esc — выйти из полного экрана")


    # ---------- Background copy/move queue ----------
    def _unique_reserved_destination(self, target: Path) -> Path:
        target = Path(target)
        if not target.exists() and not self.file_reservations.is_busy(target):
            return target
        stem, suffix = target.stem, target.suffix
        number = 1
        while True:
            candidate = target.with_name(f"{stem} ({number}){suffix}")
            if not candidate.exists() and not self.file_reservations.is_busy(candidate):
                return candidate
            number += 1

    def enqueue_file_operation(self, kind, source, target, action_label, index, remove_from_list=False):
        target = self._unique_reserved_destination(Path(target))
        op_id = uuid.uuid4().hex
        info = {
            "id": op_id,
            "kind": kind,
            "source": Path(source),
            "target": target,
            "action_label": action_label,
            "index": int(index),
            "remove_from_list": bool(remove_from_list),
        }
        self.file_reservations.acquire(op_id, source, target)
        self.file_operation_queue.append(info)
        self.append_log("QUEUE", source, target, "QUEUED", action_label)
        self.update_background_queue_ui()
        self._start_next_file_operation()
        return target

    def _start_next_file_operation(self):
        if self.active_file_operation is not None or not self.file_operation_queue:
            return
        info = self.file_operation_queue.pop(0)
        self.active_file_operation = info
        task = FileOperationTask(info["id"], info["kind"], info["source"], info["target"])
        info["task"] = task
        task.signals.progress.connect(self._on_file_operation_progress)
        task.signals.finished.connect(self._on_file_operation_finished)
        task.signals.finished.connect(lambda *_args, t=task: None)
        self.background_progress.setRange(0, 100)
        self.background_progress.setValue(0)
        self.update_background_queue_ui()
        try:
            self.thread_pool.start(task)
        except Exception as exc:
            # Defer completion until the caller has updated its file list.
            QTimer.singleShot(0, self, lambda op=info["id"], target=str(info["target"]), error=str(exc):
                              self._on_file_operation_finished(op, False, target, error))

    def _on_file_operation_progress(self, op_id, copied, total):
        if not self.active_file_operation or self.active_file_operation["id"] != op_id:
            return
        if total > 0:
            self.background_progress.setRange(0, 100)
            self.background_progress.setValue(int(min(100, copied * 100 / total)))
        else:
            self.background_progress.setRange(0, 0)

    def _on_file_operation_finished(self, op_id, success, result_path, error):
        info = self.active_file_operation
        if not info or info["id"] != op_id:
            return
        source = info["source"]
        target = Path(result_path)
        self.file_reservations.release(op_id)

        if success:
            self.append_log(info["action_label"], source, target, "OK", "Фоновая операция завершена")
            self.undo_stack.append({
                "kind": info["kind"],
                "source": source,
                "result": target,
                "index": info["index"],
            })
        else:
            self.append_log(info["action_label"], source, target, "ERROR", error)
            if info["remove_from_list"] and source.exists() and source not in self.files:
                insert_at = max(0, min(info["index"], len(self.files)))
                self.files.insert(insert_at, source)
                if self.current_index >= insert_at:
                    self.current_index += 1
                self.refresh_thumbnail_ribbon()
            QMessageBox.critical(self, APP_NAME, f"Фоновая операция не выполнена:\n{error}")

        self.active_file_operation = None
        self.background_progress.setRange(0, 100)
        self.background_progress.setValue(0)
        self.update_controls()
        self.update_background_queue_ui()
        self._start_next_file_operation()

    def update_background_queue_ui(self):
        self.background_widget.setVisible(bool(self.active_file_operation or self.file_operation_queue))
        queued = len(self.file_operation_queue)
        if self.active_file_operation:
            name = self.active_file_operation["source"].name
            self.background_label.setText(f"Фоновая операция: {name} • в очереди: {queued}")
        elif queued:
            self.background_label.setText(f"Фоновые операции в очереди: {queued}")
        else:
            self.background_label.setText("Фоновые операции: нет")
            self.background_progress.setRange(0, 100)
            self.background_progress.setValue(0)

    # ---------- Rename/action logic ----------
    def filter_duplicate_tags(self, path: Path, tags):
        return filter_duplicate_tags(path, tags, self.categories)

    def render_rename(self, path: Path, tags, index):
        return render_rename(path, tags, index, self.settings.get("rename_template", DEFAULT_TEMPLATE))

    def _effective_action(self, category_names):
        configs = [self.category_config(name) for name in category_names]
        configs = [x for x in configs if x]
        if not configs:
            return None
        first_action = configs[0].get("action", "rename")
        first_dest = str(configs[0].get("destination", "")).strip()
        for config in configs[1:]:
            action = config.get("action", "rename")
            dest = str(config.get("destination", "")).strip()
            if action != first_action or Path(dest) != Path(first_dest):
                return "CONFLICT"
        return {"action": first_action, "destination": first_dest}

    def process_categories(self, category_names):
        if not self.current_file or not self.current_file.exists():
            return
        if self._warn_if_busy(self.current_file):
            return

        action_config = self._effective_action(category_names)
        if action_config == "CONFLICT":
            QMessageBox.warning(
                self,
                APP_NAME,
                "Для нескольких тегов выбраны категории с разными действиями или папками назначения. "
                "Назначьте им одинаковое действие либо применяйте отдельно.",
            )
            return
        if not action_config:
            return

        old_path = self.current_file
        action = action_config["action"]
        destination = action_config["destination"]
        new_tags, skipped_tags = self.filter_duplicate_tags(old_path, category_names)

        if skipped_tags:
            self.append_log(
                "DUPLICATE_PREFIX",
                old_path,
                status="SKIP",
                detail="Уже присутствуют: " + ", ".join(skipped_tags),
            )

        # Actions that rename need at least one genuinely new prefix. If every
        # requested prefix already exists, don't manufacture a duplicate name.
        needs_rename = action in ACTIONS_WITH_RENAME
        if needs_rename and not new_tags:
            if action == "rename":
                self.append_log("RENAME", old_path, old_path, "SKIP", "Все выбранные префиксы уже есть")
                self.current_index += 1
                self.show_current_file()
                return
            # rename+move/copy degrades to move/copy when prefix is already present.
            action = "move" if action == "rename_move" else "copy"
            needs_rename = False

        if old_path.suffix.lower() in VIDEO_EXTENSIONS and action in {"rename", "move", "rename_move"}:
            self.stop_active_video()

        try:
            if action == "rename":
                new_name = self.render_rename(old_path, new_tags, self.current_index)
                target = old_path.with_name(new_name)
                if target == old_path:
                    self.append_log("RENAME", old_path, target, "SKIP", "Шаблон не изменил имя")
                    self.current_index += 1
                    self.show_current_file()
                    return
                if self._warn_if_busy(target):
                    self.show_current_file()
                    return
                if target.exists():
                    QMessageBox.warning(self, APP_NAME, f"Файл уже существует:\n{target.name}")
                    self.append_log("RENAME", old_path, target, "ERROR", "Имя уже занято")
                    self.show_current_file()
                    return
                rename_no_replace(old_path, target)
                self.undo_stack.append({"kind": "rename", "source": old_path, "result": target, "index": self.current_index})
                self.files[self.current_index] = target
                self._rename_cache_key(old_path, target)
                self.append_log("RENAME", old_path, target)
                self.current_index += 1

            elif action in {"move", "rename_move"}:
                dest_dir = Path(destination).expanduser()
                dest_dir.mkdir(parents=True, exist_ok=True)
                name = self.render_rename(old_path, new_tags, self.current_index) if action == "rename_move" else old_path.name
                target = dest_dir / name
                try:
                    if target.resolve() == old_path.resolve():
                        self.append_log("MOVE", old_path, target, "SKIP", "Источник и назначение совпадают")
                        self.current_index += 1
                        self.show_current_file()
                        return
                except Exception:
                    pass
                queue_index = self.current_index
                label = "RENAME+MOVE" if action == "rename_move" else "MOVE"
                self.enqueue_file_operation("move", old_path, target, label, queue_index, remove_from_list=True)
                self._drop_cache_path(old_path)
                self.thumbnail_cache.pop(str(old_path), None)
                self.files.pop(self.current_index)

            elif action in {"copy", "rename_copy"}:
                dest_dir = Path(destination).expanduser()
                dest_dir.mkdir(parents=True, exist_ok=True)
                name = self.render_rename(old_path, new_tags, self.current_index) if action == "rename_copy" else old_path.name
                target = dest_dir / name
                label = "RENAME+COPY" if action == "rename_copy" else "COPY"
                self.enqueue_file_operation("copy", old_path, target, label, self.current_index, remove_from_list=False)
                self.current_index += 1

        except Exception as exc:
            self.append_log(ACTION_LABELS.get(action, action), old_path, status="ERROR", detail=str(exc))
            QMessageBox.critical(self, APP_NAME, f"Не удалось выполнить действие:\n{exc}")

        self.clear_selected_tags()
        self.show_current_file()

    def _drop_cache_path(self, path: Path):
        key = str(path)
        image = self.image_cache.pop(key, None)
        if image is not None:
            self.image_cache_bytes -= self._image_bytes(image)

    def _rename_cache_key(self, old_path: Path, new_path: Path):
        old_key = str(old_path)
        new_key = str(new_path)
        image = self.image_cache.pop(old_key, None)
        if image is not None:
            self.image_cache[new_key] = image
        thumb = self.thumbnail_cache.pop(old_key, None)
        if thumb is not None:
            self.thumbnail_cache[new_key] = thumb

    def apply_selected_tags(self):
        if self.multi_btn.isChecked() and self.selected_tags:
            self.process_categories(self.selected_tags.copy())

    def update_tag_preview(self):
        if not self.current_file:
            self.tag_preview_label.setText("")
            return
        template = self.settings.get("rename_template", DEFAULT_TEMPLATE)
        if self.multi_btn.isChecked():
            if self.selected_tags:
                new_tags, skipped = self.filter_duplicate_tags(self.current_file, self.selected_tags)
                preview = self.render_rename(self.current_file, new_tags, self.current_index) if new_tags else self.current_file.name
                suffix = f" • уже есть: {', '.join(skipped)}" if skipped else ""
                self.tag_preview_label.setText(
                    f"Теги: {', '.join(self.selected_tags)} → {preview}{suffix} • шаблон: {template}"
                )
            else:
                self.tag_preview_label.setText("Выберите несколько категорий и нажмите Enter / «Применить теги →».")
        else:
            self.tag_preview_label.setText(f"Шаблон: {template} • категория сразу выполняет назначенное ей действие.")

    # ---------- Undo (kept from V2, extended for new actions) ----------
    def undo_last_action(self):
        if not self.undo_stack:
            return
        action = self.undo_stack[-1]
        kind = action["kind"]
        source = Path(action["source"])
        result = Path(action["result"])
        index = int(action.get("index", 0))
        if self._warn_if_busy(source, result):
            return
        self.stop_all_video()

        try:
            if kind == "rename":
                if not result.exists():
                    raise FileNotFoundError(f"Не найден файл: {result}")
                if source.exists():
                    raise FileExistsError(f"Исходное имя уже занято: {source}")
                rename_no_replace(result, source)
                try:
                    list_index = self.files.index(result)
                    self.files[list_index] = source
                    self.current_index = list_index
                except ValueError:
                    self.files.append(source)
                    self.files.sort(key=lambda p: p.name.lower())
                    self.current_index = self.files.index(source)
                self._rename_cache_key(result, source)

            elif kind == "move":
                if not result.exists():
                    raise FileNotFoundError(f"Не найден перемещённый файл: {result}")
                if source.exists():
                    raise FileExistsError(f"Исходный путь уже занят: {source}")
                source.parent.mkdir(parents=True, exist_ok=True)
                restored = perform_file_operation("move", result, source)
                insert_at = max(0, min(index, len(self.files)))
                self.files.insert(insert_at, restored)
                self.current_index = insert_at

            elif kind == "copy":
                if not result.exists():
                    raise FileNotFoundError(f"Копия уже отсутствует: {result}")
                result.unlink()
                try:
                    self.current_index = self.files.index(source)
                except ValueError:
                    self.current_index = max(0, min(index, len(self.files) - 1))

            self.undo_stack.pop()
            self.append_log("UNDO", result, source if kind != "copy" else "", "OK", kind)
            self.show_current_file()
        except Exception as exc:
            self.append_log("UNDO", result, source, "ERROR", str(exc))
            QMessageBox.critical(self, APP_NAME, f"Не удалось отменить действие:\n{exc}")

    # ---------- Navigation ----------
    def next_file(self):
        if not self.files:
            return
        if self.current_file and self.current_file.exists():
            self.append_log("SKIP", self.current_file, status="OK", detail="Ручной переход к следующему файлу")
        self.current_index += 1
        self.show_current_file()

    def previous_file(self):
        if not self.files:
            return
        self.current_index = max(0, self.current_index - 1)
        self.show_current_file()

    def _warn_if_busy(self, *paths):
        if not self.file_reservations.is_busy(*paths):
            return False
        QMessageBox.information(self, APP_NAME,
                                "Файл занят копированием/перемещением. Дождитесь завершения операции.")
        return True

    def _can_undo(self):
        if not self.undo_stack:
            return False
        action = self.undo_stack[-1]
        return not self.file_reservations.is_busy(action["source"], action["result"])

    def update_controls(self):
        has_current = bool(self.current_file and self.current_file.exists())
        self.left_nav_btn.setEnabled(bool(self.files and self.current_index > 0))
        self.right_nav_btn.setEnabled(bool(self.files and has_current and self.current_index < len(self.files) - 1))
        self.undo_btn.setEnabled(self._can_undo())
        busy = has_current and self.file_reservations.is_busy(self.current_file)
        self.edit_photo_btn.setEnabled(bool(has_current and not busy and self.current_file.suffix.lower() in SUPPORTED_EXTENSIONS))
        for button in self.category_buttons.values():
            button.setEnabled(has_current and not busy)
        self.undo_btn.setToolTip("Дождитесь завершения операции с этим файлом"
                                 if self.undo_stack and not self._can_undo() else "Отменить последнее завершённое действие")
        self.apply_tags_btn.setVisible(self.multi_btn.isChecked())
        self.apply_tags_btn.setEnabled(self.multi_btn.isChecked() and has_current and not busy and bool(self.selected_tags))
        self.rotate_left_btn.setEnabled(has_current)
        self.rotate_right_btn.setEnabled(has_current)
        is_video = has_current and self.current_file.suffix.lower() in VIDEO_EXTENSIONS
        for widget in (self.play_btn, self.minus5_btn, self.plus5_btn, self.frame_back_btn, self.frame_next_btn, self.timeline, self.speed_combo):
            widget.setEnabled(bool(is_video))

    def closeEvent(self, event):
        if self.active_file_operation is not None or self.file_operation_queue:
            QMessageBox.information(
                self, APP_NAME,
                "Сейчас выполняется копирование/перемещение файлов. Дождитесь завершения фоновой очереди, "
                "чтобы не оставить неполный файл."
            )
            event.ignore()
            return
        self.stop_all_video()
        self.settings["window_maximized"] = self.isMaximized() or self.fullscreen_mode
        if hasattr(self, 'startup_updates'):
            self.startup_updates.stop()
        self._persist_settings()
        super().closeEvent(event)


if __name__ == "__main__":
    # Keep a named mutex open so setup/update cannot replace a running app.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        _install_guard = ctypes.windll.kernel32.CreateMutexW(None, False, "MediaCategorizer.Running")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MediaCategorizer()
    window.show()
    from media_categorizer.updates import StartupUpdates
    window.startup_updates = StartupUpdates(window)
    QTimer.singleShot(1000, window.startup_updates.start)
    sys.exit(app.exec())
