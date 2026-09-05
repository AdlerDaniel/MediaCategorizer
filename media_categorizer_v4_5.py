import csv
import hashlib
import json
import os
import shutil
import sys
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QRect, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPixmap,
    QTransform,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoSink, QtVideo
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
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

APP_NAME = "Media Categorizer 4.5"
APP_FOLDER = "MediaCategorizer"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm", ".mpeg", ".mpg"
}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

ACTION_LABELS = OrderedDict([
    ("rename", "Переименовать"),
    ("move", "Переместить"),
    ("copy", "Скопировать"),
    ("rename_move", "Переименовать + переместить"),
    ("rename_copy", "Переименовать + скопировать"),
])
ACTIONS_WITH_DESTINATION = {"move", "copy", "rename_move", "rename_copy"}
ACTIONS_WITH_RENAME = {"rename", "rename_move", "rename_copy"}

DEFAULT_CATEGORIES = [
    {"name": "GOOD", "shortcut": "1", "action": "rename", "destination": ""},
    {"name": "BAD", "shortcut": "2", "action": "rename", "destination": ""},
    {"name": "EDIT", "shortcut": "3", "action": "rename", "destination": ""},
    {"name": "POST", "shortcut": "4", "action": "rename", "destination": ""},
]

DEFAULT_TEMPLATE = "{tags}_{original}"
TEMPLATE_PRESETS = [
    "{tags}_{original}",
    "{tags}_{stem}{ext}",
    "{date}_{tags}_{original}",
    "{tags}_{index}_{original}",
    "{category}_{stem}{ext}",
]

# Image preloading is asynchronous. The cache is capped both by count and memory.
IMAGE_PRELOAD_FORWARD = 5
IMAGE_PRELOAD_BACKWARD = 2
IMAGE_CACHE_MAX_ITEMS = 12
IMAGE_CACHE_MAX_BYTES = 512 * 1024 * 1024

# Three video decoders: one active + up to two upcoming videos pre-decoded to first frame.
VIDEO_SLOT_COUNT = 2
VIDEO_PRELOAD_COUNT = 1
VIDEO_SCAN_AHEAD = 8

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


def app_config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    folder = base / APP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def settings_path() -> Path:
    return app_config_dir() / "settings_v4.json"


def v3_settings_path() -> Path:
    return app_config_dir() / "settings_v3.json"


def v2_settings_path() -> Path:
    return app_config_dir() / "settings_v2.json"


def log_path() -> Path:
    return app_config_dir() / "processing_log_v4.jsonl"


def normalize_categories(data):
    result = []
    if isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, str):
                name = item.strip()
                shortcut = str(index + 1) if index < 9 else ""
                action = "rename"
                destination = ""
            elif isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                shortcut = str(item.get("shortcut", "")).strip()
                action = str(item.get("action", "rename")).strip()
                destination = str(item.get("destination", "")).strip()
            else:
                continue

            if not name:
                continue
            if action not in ACTION_LABELS:
                action = "rename"
            shortcut = QKeySequence(shortcut).toString(QKeySequence.PortableText)
            result.append({
                "name": name,
                "shortcut": shortcut,
                "action": action,
                "destination": destination,
            })
    return result or [dict(x) for x in DEFAULT_CATEGORIES]


def load_settings():
    defaults = {
        "categories": [dict(x) for x in DEFAULT_CATEGORIES],
        "last_folder": "",
        "multi_tag_mode": False,
        "loop_video": True,
        "muted": False,
        "playback_rate": 1.0,
        "rename_template": DEFAULT_TEMPLATE,
        "window_maximized": True,
    }

    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
                defaults["categories"] = normalize_categories(defaults.get("categories"))
                return defaults
        except Exception:
            pass

    # Migrate useful settings from V3 automatically.
    old3 = v3_settings_path()
    if old3.exists():
        try:
            data = json.loads(old3.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
                defaults["categories"] = normalize_categories(defaults.get("categories"))
                return defaults
        except Exception:
            pass

    # Migrate useful settings from V2 automatically.
    old = v2_settings_path()
    if old.exists():
        try:
            data = json.loads(old.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("last_folder", "multi_tag_mode", "loop_video", "muted"):
                    if key in data:
                        defaults[key] = data[key]
                defaults["categories"] = normalize_categories(data.get("categories"))
        except Exception:
            pass

    return defaults


def save_settings(data):
    settings_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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


def safe_filename(name: str) -> str:
    forbidden = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in forbidden else ch for ch in name)
    cleaned = cleaned.strip().rstrip(". ")
    return cleaned or "media"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    number = 1
    while True:
        candidate = path.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists():
            return candidate
        number += 1


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
    loaded = Signal(str, QImage)


class ImageLoadTask(QRunnable):
    def __init__(self, path: Path):
        super().__init__()
        self.path = Path(path)
        self.signals = ImageLoadSignals()

    def run(self):
        image = read_oriented_image(self.path) if self.path.exists() else QImage()
        self.signals.loaded.emit(str(self.path), image)


class MediaViewport(QWidget):
    """Clipping viewport for photo/video display.

    At 100% the current media is fitted to the entire free viewer area while
    preserving its aspect ratio. Values above 100% enlarge from that fitted
    size and are clipped by this viewport instead of forcing the surrounding
    toolbars/panels to move.
    """

    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class ImageCanvas(QWidget):
    """Photo viewport that always uses the whole free viewer area.

    100% means fit the complete image to the current canvas, edge-to-edge on
    the limiting axis while preserving aspect ratio. Zoom above 100% grows
    from that fitted size and is clipped by the canvas; surrounding controls
    never move or shrink the media area.
    """

    def __init__(self, message="", parent=None):
        super().__init__(parent)
        self._image = QImage()
        self._message = str(message or "")
        self._zoom_percent = 100
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def clear(self):
        self._image = QImage()
        self._message = ""
        self.update()

    def setText(self, text):
        self._message = str(text or "")
        self._image = QImage()
        self.update()

    def text(self):
        return self._message

    def set_image(self, image: QImage):
        self._image = QImage(image) if image is not None and not image.isNull() else QImage()
        if not self._image.isNull():
            self._message = ""
        self.update()

    def set_zoom_percent(self, percent):
        self._zoom_percent = max(25, min(400, int(percent)))
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if self._image.isNull():
            if self._message:
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            painter.end()
            return

        area = self.contentsRect()
        aw = max(1, area.width())
        ah = max(1, area.height())
        sw = max(1, self._image.width())
        sh = max(1, self._image.height())

        # Base size for 100%: the largest complete image that fits in every
        # available pixel of the viewer. This intentionally does NOT use the
        # source's physical pixel size, so a small image is enlarged too.
        fit = min(aw / sw, ah / sh)
        factor = fit * max(0.25, min(4.0, self._zoom_percent / 100.0))
        tw = max(1, int(round(sw * factor)))
        th = max(1, int(round(sh * factor)))
        x = area.x() + int(round((aw - tw) / 2))
        y = area.y() + int(round((ah - th) / 2))

        target = QRect(x, y, tw, th)
        painter.drawImage(target, self._image)
        painter.end()


class VideoCanvas(QVideoWidget):
    """Native Qt video surface with adaptive geometry.

    QVideoWidget keeps the decoded frame in Qt's video pipeline instead of
    converting every 4K/60fps frame to QImage in Python.  This is the main
    playback-performance change in V4.2.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_size = QSize(16, 9)
        self._auto_rotation = 0
        self._manual_rotation = 0
        self._zoom_percent = 100
        self.setMinimumSize(1, 1)
        self.setAspectRatioMode(Qt.KeepAspectRatio)

    @property
    def manual_rotation(self):
        return self._manual_rotation

    def clear(self):
        self._source_size = QSize(16, 9)
        self._auto_rotation = 0
        self._manual_rotation = 0
        try:
            self.videoSink().setVideoFrame(QVideoFrame())
        except Exception:
            pass
        self.updateGeometry()
        self.update()

    def set_video_geometry(self, size: QSize, auto_rotation=0):
        changed = False
        if size is not None and size.isValid() and size.width() > 0 and size.height() > 0:
            new_size = QSize(size)
            if new_size != self._source_size:
                self._source_size = new_size
                changed = True
        new_rotation = int(auto_rotation or 0) % 360
        if new_rotation != self._auto_rotation:
            self._auto_rotation = new_rotation
            changed = True
        # Do not trigger a QWidget layout pass for every 30/60 fps frame.
        if changed:
            self.fit_to_parent()

    def rotate_view(self, degrees):
        self._manual_rotation = (self._manual_rotation + int(degrees)) % 360
        self.fit_to_parent()
        return self._manual_rotation

    def reset_manual_rotation(self):
        self._manual_rotation = 0
        self.fit_to_parent()

    def set_zoom_percent(self, percent):
        self._zoom_percent = max(25, min(400, int(percent)))
        self.fit_to_parent()

    def effective_rotation(self):
        return (self._auto_rotation + self._manual_rotation) % 360

    def _effective_size(self):
        width = max(1, self._source_size.width())
        height = max(1, self._source_size.height())
        if self.effective_rotation() % 180:
            width, height = height, width
        return QSize(width, height)

    def fit_to_parent(self):
        parent = self.parentWidget()
        if parent is None:
            return
        available = parent.contentsRect().size()
        if available.width() <= 1 or available.height() <= 1:
            return

        source = self._effective_size()
        ratio = max(1, source.width()) / max(1, source.height())

        # 100% means "fit to all currently available viewer space". Zooming
        # is relative to that fitted size, not to the media's physical pixels.
        base_width = available.width()
        base_height = int(round(base_width / ratio))
        if base_height > available.height():
            base_height = available.height()
            base_width = int(round(base_height * ratio))

        factor = max(0.25, min(4.0, self._zoom_percent / 100.0))
        width = max(1, int(round(base_width * factor)))
        height = max(1, int(round(base_height * factor)))
        x = int(round((available.width() - width) / 2))
        y = int(round((available.height() - height) / 2))
        self.setGeometry(x, y, width, height)


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
        self.resize(950, 560)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            "Категория", "Горячая клавиша", "Действие", "Папка назначения"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)

        add_button = QPushButton("Добавить")
        delete_button = QPushButton("Удалить")
        up_button = QPushButton("Выше")
        down_button = QPushButton("Ниже")
        save_button = QPushButton("Сохранить")
        cancel_button = QPushButton("Отмена")

        add_button.clicked.connect(self.add_row)
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
        self.set_categories(categories)

    def set_categories(self, categories):
        self.table.setRowCount(0)
        for item in categories:
            self.add_row(
                item.get("name", ""),
                item.get("shortcut", ""),
                item.get("action", "rename"),
                item.get("destination", ""),
            )

    def add_row(self, name="", shortcut="", action="rename", destination=""):
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
    def __init__(self, path: Path, size=QSize(160, 96)):
        super().__init__()
        self.path = Path(path)
        self.size = QSize(size)
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
        self.signals.loaded.emit(str(self.path), image)


class FileOperationSignals(QObject):
    progress = Signal(str, int, int)
    finished = Signal(str, bool, str, str)


class FileOperationTask(QRunnable):
    def __init__(self, op_id: str, kind: str, source: Path, target: Path):
        super().__init__()
        self.op_id = op_id
        self.kind = kind
        self.source = Path(source)
        self.target = Path(target)
        self.signals = FileOperationSignals()

    def _copy_with_progress(self):
        total = max(0, self.source.stat().st_size)
        copied = 0
        self.target.parent.mkdir(parents=True, exist_ok=True)
        with self.source.open("rb") as src, self.target.open("wb") as dst:
            while True:
                chunk = src.read(16 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                self.signals.progress.emit(self.op_id, copied, total)
        shutil.copystat(self.source, self.target, follow_symlinks=True)
        self.signals.progress.emit(self.op_id, total, total)

    def run(self):
        try:
            if not self.source.exists():
                raise FileNotFoundError(f"Исходный файл не найден: {self.source}")
            self.target.parent.mkdir(parents=True, exist_ok=True)
            if self.target.exists():
                raise FileExistsError(f"Файл назначения уже существует: {self.target}")

            if self.kind == "copy":
                self._copy_with_progress()
            elif self.kind == "move":
                same_device = False
                try:
                    same_device = self.source.stat().st_dev == self.target.parent.stat().st_dev
                except Exception:
                    pass
                if same_device:
                    os.replace(self.source, self.target)
                    total = max(0, self.target.stat().st_size)
                    self.signals.progress.emit(self.op_id, total, total)
                else:
                    self._copy_with_progress()
                    self.source.unlink()
            else:
                raise ValueError(f"Неизвестная операция: {self.kind}")
            self.signals.finished.emit(self.op_id, True, str(self.target), "")
        except Exception as exc:
            # Remove a partially copied destination when it is safe to do so.
            try:
                if self.kind in {"copy", "move"} and self.target.exists() and self.source.exists():
                    self.target.unlink()
            except Exception:
                pass
            self.signals.finished.emit(self.op_id, False, str(self.target), str(exc))


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
        self.reserved_targets = set()

        self.image_cache = OrderedDict()
        self.image_cache_bytes = 0
        self.pending_image_loads = set()
        self.thread_pool = QThreadPool.globalInstance()
        self.thread_pool.setMaxThreadCount(max(2, min(4, self.thread_pool.maxThreadCount())))

        self.video_slots = [self._create_video_slot(i) for i in range(VIDEO_SLOT_COUNT)]
        self.active_video_slot = 0
        self.timeline_dragging = False
        self._native_frame_guard = False

        self._build_ui()
        self._load_toggle_settings()
        self.build_category_buttons()
        self.create_global_shortcuts()
        self._update_last_folder_button()
        self.update_controls()

        if bool(self.settings.get("window_maximized", True)):
            QTimer.singleShot(0, self.showMaximized)

    # ---------- UI ----------
    def _build_ui(self):
        self.progress_label = QLabel("0 / 0 • осталось: 0")
        self.progress_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.fullscreen_status_label = QLabel("0 / 0 • осталось: 0   •   F11 / Esc — выйти из полного экрана")
        self.fullscreen_status_label.setAlignment(Qt.AlignCenter)
        self.fullscreen_status_label.hide()

        self.open_folder_btn = QPushButton("Открыть папку")
        self.last_folder_btn = QPushButton("Последняя папка")
        self.open_file_btn = QPushButton("Открыть файл")
        self.undo_btn = QPushButton("Отменить Ctrl+Z")
        self.settings_btn = QPushButton("Категории / действия")
        self.template_btn = QPushButton("Шаблон имени")
        self.log_btn = QPushButton("Журнал")
        self.duplicates_btn = QPushButton("Дубликаты")
        self.fullscreen_btn = QPushButton("Полный экран F11")

        self.open_folder_btn.clicked.connect(self.open_folder)
        self.last_folder_btn.clicked.connect(self.open_last_folder)
        self.open_file_btn.clicked.connect(self.open_file)
        self.undo_btn.clicked.connect(self.undo_last_action)
        self.settings_btn.clicked.connect(self.edit_categories)
        self.template_btn.clicked.connect(self.edit_rename_template)
        self.log_btn.clicked.connect(self.show_log)
        self.duplicates_btn.clicked.connect(self.show_duplicates)
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        top_layout = QHBoxLayout()
        for button in (
            self.open_folder_btn, self.last_folder_btn, self.open_file_btn,
            self.undo_btn, self.settings_btn, self.template_btn,
            self.log_btn, self.duplicates_btn, self.fullscreen_btn,
        ):
            top_layout.addWidget(button)
        top_layout.addStretch()
        top_layout.addWidget(self.progress_label)
        self.top_widget = QWidget()
        self.top_widget.setLayout(top_layout)

        self.multi_btn = QPushButton()
        self.multi_btn.setCheckable(True)
        self.apply_tags_btn = QPushButton("Применить теги →")
        self.loop_btn = QPushButton()
        self.loop_btn.setCheckable(True)
        self.sound_btn = QPushButton()
        self.sound_btn.setCheckable(True)
        self.rotate_left_btn = QPushButton("↺ Вид")
        self.rotate_right_btn = QPushButton("↻ Вид")
        self.thumbnail_toggle_btn = QPushButton("Миниатюры ▸")
        self.thumbnail_toggle_btn.setCheckable(True)
        self.properties_toggle_btn = QPushButton("Параметры ▸")
        self.properties_toggle_btn.setCheckable(True)

        self.zoom_out_btn = QPushButton("−")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_reset_btn = QPushButton("100%")
        self.zoom_reset_btn.setToolTip("Сбросить масштаб: 100% = вписать в свободную область")
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
        tools_layout.addWidget(self.multi_btn)
        tools_layout.addWidget(self.apply_tags_btn)
        tools_layout.addWidget(self.loop_btn)
        tools_layout.addWidget(self.sound_btn)
        tools_layout.addWidget(self.rotate_left_btn)
        tools_layout.addWidget(self.rotate_right_btn)
        tools_layout.addSpacing(8)
        tools_layout.addWidget(QLabel("Масштаб"))
        tools_layout.addWidget(self.zoom_out_btn)
        tools_layout.addWidget(self.zoom_slider)
        tools_layout.addWidget(self.zoom_in_btn)
        tools_layout.addWidget(self.zoom_reset_btn)
        tools_layout.addSpacing(8)
        tools_layout.addWidget(self.thumbnail_toggle_btn)
        tools_layout.addWidget(self.properties_toggle_btn)
        tools_layout.addStretch()
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
        self.video_host = MediaViewport()
        self.video_host.setMinimumSize(1, 1)
        self.video_host.setContentsMargins(0, 0, 0, 0)
        self.video_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_canvas = VideoCanvas(self.video_host)
        self.video_canvas.videoSink().videoFrameChanged.connect(self._on_active_native_video_frame)
        self.video_host.resized.connect(self.video_canvas.fit_to_parent)

        self.media_pages.addWidget(self.image_label)
        self.media_pages.addWidget(self.video_host)
        self.media_pages.setCurrentWidget(self.image_label)
        self.video_canvas.hide()
        self.media_stack.resized.connect(self._on_media_viewport_resized)

        # Large navigation arrows live immediately beside the viewed media.
        self.left_nav_btn = QPushButton("❮")
        self.right_nav_btn = QPushButton("❯")
        for btn in (self.left_nav_btn, self.right_nav_btn):
            btn.setFixedWidth(54)
            btn.setMinimumHeight(160)
            btn.setStyleSheet("font-size: 30px; font-weight: 600;")
        self.left_nav_btn.setToolTip("Предыдущий файл (←)")
        self.right_nav_btn.setToolTip("Следующий файл (→)")
        self.left_nav_btn.clicked.connect(self.previous_file)
        self.right_nav_btn.clicked.connect(self.next_file)

        # Video transport controls: timeline and controls are centered directly
        # below the video instead of stretching from the left edge of the window.
        self.play_btn = QPushButton("▶")
        self.minus5_btn = QPushButton("−5 с")
        self.plus5_btn = QPushButton("+5 с")
        self.frame_back_btn = QPushButton("◀ кадр")
        self.frame_next_btn = QPushButton("кадр ▶")
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
        viewer_row.addWidget(self.media_stack, 1)
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

        self.filename_label = QLabel("")
        self.filename_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.filename_label.setWordWrap(True)
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

        self.category_widget = QWidget()
        self.category_layout = QHBoxLayout(self.category_widget)
        self.category_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.top_widget)
        layout.addWidget(self.tools_widget)
        layout.addWidget(self.fullscreen_status_label)
        layout.addWidget(self.media_row_widget, 1)
        layout.addWidget(self.filename_label)
        layout.addWidget(self.tag_preview_label)
        layout.addWidget(self.thumbnail_list)
        layout.addWidget(self.scroll)
        self.setCentralWidget(central)

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
        self.multi_btn.setText("Несколько тегов: вкл" if self.multi_btn.isChecked() else "Несколько тегов: выкл")
        self.loop_btn.setText("Повтор: вкл" if self.loop_btn.isChecked() else "Повтор: выкл")
        self.sound_btn.setText("Звук: вкл" if self.sound_btn.isChecked() else "Звук: выкл")

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
            QTimer.singleShot(0, self._on_media_viewport_resized)

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
        QTimer.singleShot(0, self._on_media_viewport_resized)

    def set_thumbnail_ribbon_visible(self, enabled):
        self.thumbnail_toggle_btn.setText("Миниатюры ▾" if enabled else "Миниатюры ▸")
        self.thumbnail_list.setVisible(bool(enabled))
        if enabled:
            self.refresh_thumbnail_ribbon()
        QTimer.singleShot(0, self._on_media_viewport_resized)

    def set_properties_visible(self, enabled):
        self.properties_toggle_btn.setText("Параметры ▾" if enabled else "Параметры ▸")
        self.properties_group.setVisible(bool(enabled))
        if enabled:
            self.update_file_properties()
        QTimer.singleShot(0, self._on_media_viewport_resized)

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
            QTimer.singleShot(0, self.video_canvas.fit_to_parent)

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
            button = QPushButton(text)
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
        for index, slot in enumerate(self.video_slots):
            slot["audio"].setMuted(not enabled or index != self.active_video_slot)
        self._persist_settings()

    def edit_categories(self):
        dialog = CategoriesDialog(self.categories, self)
        if dialog.exec():
            self.categories = dialog.result_categories()
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
        return safe_filename(str(category)).strip("_")

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
        task = ImageLoadTask(path)
        task.signals.loaded.connect(self._on_image_preloaded)
        # Keep task/signals alive until the queued signal is delivered.
        task.signals.loaded.connect(lambda _p, _i, t=task: None)
        self.thread_pool.start(task)

    def _on_image_preloaded(self, path_str, image):
        self.pending_image_loads.discard(path_str)
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
        task = ThumbnailTask(path)
        task.signals.loaded.connect(self._on_thumbnail_loaded)
        task.signals.loaded.connect(lambda _p, _i, t=task: None)
        self.thread_pool.start(task)

    def _on_thumbnail_loaded(self, path_str, image):
        self.pending_thumbnail_loads.discard(path_str)
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
        self.play_btn.setText("⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

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
        self.play_btn.setText("▶")


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
        QTimer.singleShot(0, self._on_media_viewport_resized)
        QTimer.singleShot(0, self._preload_window)
        QTimer.singleShot(900, self._preload_video_window)

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
        QTimer.singleShot(0, self._on_media_viewport_resized)

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
        if not target.exists() and str(target).casefold() not in self.reserved_targets:
            return target
        stem, suffix = target.stem, target.suffix
        number = 1
        while True:
            candidate = target.with_name(f"{stem} ({number}){suffix}")
            if not candidate.exists() and str(candidate).casefold() not in self.reserved_targets:
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
        self.reserved_targets.add(str(target).casefold())
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
        self.thread_pool.start(task)

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
        self.reserved_targets.discard(str(info["target"]).casefold())

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
    def leading_category_prefixes(self, path: Path):
        remainder = path.stem
        found = []
        category_prefixes = []
        for item in self.categories:
            clean = self.sanitize_category(item["name"])
            if clean:
                category_prefixes.append((clean, clean.casefold()))
        category_prefixes.sort(key=lambda pair: len(pair[0]), reverse=True)

        while remainder:
            matched = False
            lower = remainder.casefold()
            for original, folded in category_prefixes:
                if lower == folded:
                    found.append(folded)
                    remainder = ""
                    matched = True
                    break
                prefix = folded + "_"
                if lower.startswith(prefix):
                    found.append(folded)
                    remainder = remainder[len(original) + 1:]
                    matched = True
                    break
            if not matched:
                break
        return set(found)

    def filter_duplicate_tags(self, path: Path, tags):
        existing = self.leading_category_prefixes(path)
        result = []
        skipped = []
        for tag in tags:
            clean = self.sanitize_category(tag)
            if not clean:
                continue
            if clean.casefold() in existing:
                skipped.append(clean)
            elif clean.casefold() not in {x.casefold() for x in result}:
                result.append(clean)
        return result, skipped

    def render_rename(self, path: Path, tags, index):
        tags_clean = [self.sanitize_category(x) for x in tags if self.sanitize_category(x)]
        values = {
            "tags": "_".join(tags_clean),
            "category": tags_clean[0] if tags_clean else "",
            "original": path.name,
            "stem": path.stem,
            "ext": path.suffix,
            "index": f"{index + 1:04d}",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H-%M-%S"),
        }
        template = str(self.settings.get("rename_template", DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE)
        try:
            candidate = template.format(**values)
        except Exception:
            candidate = DEFAULT_TEMPLATE.format(**values)
        candidate = safe_filename(candidate)
        if path.suffix and not candidate.casefold().endswith(path.suffix.casefold()):
            candidate += path.suffix
        return candidate

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
                if target.exists():
                    QMessageBox.warning(self, APP_NAME, f"Файл уже существует:\n{target.name}")
                    self.append_log("RENAME", old_path, target, "ERROR", "Имя уже занято")
                    self.show_current_file()
                    return
                old_path.rename(target)
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
        self.stop_all_video()

        try:
            if kind == "rename":
                if not result.exists():
                    raise FileNotFoundError(f"Не найден файл: {result}")
                if source.exists():
                    raise FileExistsError(f"Исходное имя уже занято: {source}")
                result.rename(source)
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
                restored = Path(shutil.move(str(result), str(source)))
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

    def update_controls(self):
        has_current = bool(self.current_file and self.current_file.exists())
        self.left_nav_btn.setEnabled(bool(self.files and self.current_index > 0))
        self.right_nav_btn.setEnabled(bool(self.files and has_current and self.current_index < len(self.files) - 1))
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.apply_tags_btn.setVisible(self.multi_btn.isChecked())
        self.apply_tags_btn.setEnabled(self.multi_btn.isChecked() and has_current and bool(self.selected_tags))
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
        self._persist_settings()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MediaCategorizer()
    window.show()
    sys.exit(app.exec())
