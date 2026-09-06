"""Photo editing with full-resolution crops and atomic replacement of the original."""
import hashlib
import math
from pathlib import Path
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QSaveFile, QIODevice, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QImageWriter, QPainter, QPen, QTransform
from PySide6.QtWidgets import QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QComboBox
from .ui import IconButton, COLORS
from PySide6.QtWidgets import QApplication


def fingerprint(path):
    path = Path(path)
    stat = path.stat()
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').digest()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, digest


def load_photo(path):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if reader.supportsAnimation() or reader.imageCount() > 1:
        raise ValueError('Анимация и многостраничные изображения пока не поддерживаются редактором.')
    fmt = bytes(reader.format()).lower()
    if fmt not in [bytes(value).lower() for value in QImageWriter.supportedImageFormats()]:
        raise ValueError('Для этого формата недоступно сохранение. Оригинал оставлен без изменений.')
    image = reader.read()
    if image.isNull():
        raise ValueError('Не удалось открыть фото: ' + reader.errorString())
    return image, fmt


def replace_photo(path, image, fmt, expected):
    """QSaveFile writes beside the original; commit publishes only a complete file."""
    if image.isNull():
        raise ValueError('Нельзя сохранить пустое изображение.')
    if fingerprint(path) != expected:
        raise ValueError('Фото изменено другой программой. Закройте редактор и откройте фото заново.')
    target = QSaveFile(str(path))
    target.setDirectWriteFallback(False)
    if not target.open(QIODevice.WriteOnly):
        raise OSError(target.errorString())
    try:
        writer = QImageWriter(target, fmt)
        writer.setQuality(95)
        if not writer.write(image):
            raise OSError(writer.errorString())
        if fingerprint(path) != expected:
            raise ValueError('Фото изменилось во время сохранения. Оригинал не заменён.')
        if not target.commit():
            raise OSError(target.errorString())
    finally:
        if target.isOpen():
            target.cancelWriting()
            target.commit()  # Close and discard staging immediately, even while traceback retains objects.


class CropCanvas(QWidget):
    selectionChanged = Signal()

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image
        self.selection = QRect()
        self.start = None
        self.ratio = None
        self.drag_mode = "new"
        self.drag_rect = QRect()
        self.setMinimumSize(320, 220)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName('Область обрезки фотографии')

    def image_rect(self):
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width, height = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width()-width)/2, (self.height()-height)/2, width, height)

    def source_point(self, point):
        rect = self.image_rect()
        return QPointF(max(0, min(self.image.width(), (point.x()-rect.x()) * self.image.width()/rect.width())),
                       max(0, min(self.image.height(), (point.y()-rect.y()) * self.image.height()/rect.height())))

    def clear_selection(self):
        self.selection = QRect()
        self.start = None
        self.update()
        self.selectionChanged.emit()

    def set_ratio(self, ratio):
        self.ratio = ratio
        if ratio:
            width = min(self.image.width(), self.image.height()*ratio)
            height = width/ratio
            self.selection = QRect(round((self.image.width()-width)/2), round((self.image.height()-height)/2), round(width), round(height))
            self.selectionChanged.emit()
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.image_rect().contains(event.position()):
            return
        point = self.source_point(event.position())
        self.drag_mode = 'new'
        self.drag_rect = QRect(self.selection)
        if not self.selection.isEmpty():
            r = self.selection
            threshold = 12*self.image.width()/self.image_rect().width()
            corners = [(r.x(), r.y(), r.x()+r.width(), r.y()+r.height()),
                       (r.x()+r.width(), r.y(), r.x(), r.y()+r.height()),
                       (r.x(), r.y()+r.height(), r.x()+r.width(), r.y()),
                       (r.x()+r.width(), r.y()+r.height(), r.x(), r.y())]
            for x, y, opposite_x, opposite_y in corners:
                if abs(point.x()-x) <= threshold and abs(point.y()-y) <= threshold:
                    self.start = QPointF(opposite_x, opposite_y)
                    return
            if r.contains(point.toPoint()):
                self.drag_mode = 'move'
                self.start = point
                return
        self.clear_selection()
        self.start = point
        event.accept()

    def mouseMoveEvent(self, event):
        if self.start is None:
            return
        end = self.source_point(event.position())
        if self.drag_mode == 'move':
            r = self.drag_rect
            x = max(0, min(self.image.width()-r.width(), r.x()+round(end.x()-self.start.x())))
            y = max(0, min(self.image.height()-r.height(), r.y()+round(end.y()-self.start.y())))
            self.selection = QRect(x, y, r.width(), r.height())
        else:
            dx, dy = end.x()-self.start.x(), end.y()-self.start.y()
            if self.ratio:
                sx, sy = (1 if dx >= 0 else -1), (1 if dy >= 0 else -1)
                width = max(abs(dx), abs(dy)*self.ratio)
                available_x = self.image.width()-self.start.x() if sx > 0 else self.start.x()
                available_y = self.image.height()-self.start.y() if sy > 0 else self.start.y()
                width = min(width, available_x, available_y*self.ratio)
                end = self.start + QPointF(sx*width, sy*width/self.ratio)
            left, top = math.floor(min(self.start.x(), end.x())), math.floor(min(self.start.y(), end.y()))
            right, bottom = math.ceil(max(self.start.x(), end.x())), math.ceil(max(self.start.y(), end.y()))
            self.selection = QRect(left, top, right-left, bottom-top).intersected(self.image.rect())
        self.update()
        self.selectionChanged.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.start is not None:
            self.mouseMoveEvent(event)
            self.start = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        painter.fillRect(self.rect(), QColor(c['canvas']))
        rect = self.image_rect()
        painter.drawImage(rect, self.image)
        if not self.selection.isEmpty():
            scale = rect.width()/self.image.width()
            selected = QRectF(rect.x()+self.selection.x()*scale, rect.y()+self.selection.y()*scale,
                              self.selection.width()*scale, self.selection.height()*scale)
            painter.fillRect(rect, QColor(0, 0, 0, 130))
            painter.drawImage(selected, self.image, QRectF(self.selection))
            painter.setPen(QPen(QColor('#ffffff'), 1.5))
            painter.drawRect(selected)
            for point in (selected.topLeft(), selected.topRight(), selected.bottomLeft(), selected.bottomRight()):
                painter.fillRect(QRectF(point.x()-4, point.y()-4, 8, 8), QColor("white"))
            for part in (1/3, 2/3):
                painter.drawLine(QPointF(selected.x()+selected.width()*part, selected.top()), QPointF(selected.x()+selected.width()*part, selected.bottom()))
                painter.drawLine(QPointF(selected.left(), selected.y()+selected.height()*part), QPointF(selected.right(), selected.y()+selected.height()*part))
        painter.end()


class PhotoEditor(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.expected = fingerprint(self.path)
        self.original, self.format = load_photo(self.path)
        if fingerprint(self.path) != self.expected:
            raise ValueError('Фото изменилось во время открытия. Попробуйте ещё раз.')
        self.dirty = False
        self.history = []
        self.setWindowTitle('Редактировать фото — ' + self.path.name)
        self.resize(1000, 760)
        self.canvas = CropCanvas(QImage(self.original))
        self.flip_h_btn = IconButton('flip-horizontal-2', 'Отразить ↔')
        self.flip_v_btn = IconButton('flip-vertical-2', 'Отразить ↕')
        self.crop_btn = IconButton('crop', 'Обрезать выделенное')
        self.reset_btn = IconButton('undo-2', 'Сбросить правки')
        self.undo_btn = IconButton('undo-2', 'Отменить правку')
        self.undo_btn.clicked.connect(self.undo)
        self.rotate_left = IconButton('rotate-ccw', 'Повернуть −90°', compact=True)
        self.rotate_right = IconButton('rotate-cw', 'Повернуть +90°', compact=True)
        self.rotate_left.clicked.connect(lambda: self.rotate(-90))
        self.rotate_right.clicked.connect(lambda: self.rotate(90))
        self.aspect = QComboBox()
        for label, ratio in [('Свободно', None), ('1:1', 1), ('4:5', .8), ('9:16', 9/16), ('16:9', 16/9)]:
            self.aspect.addItem(label, ratio)
        self.aspect.currentIndexChanged.connect(lambda: self.canvas.set_ratio(self.aspect.currentData()))
        self.save_btn = IconButton('check', 'Сохранить')
        self.save_btn.setProperty('primary', True)
        self.cancel_btn = IconButton('x', 'Отмена')
        self.flip_h_btn.clicked.connect(lambda: self.flip(True))
        self.flip_v_btn.clicked.connect(lambda: self.flip(False))
        self.crop_btn.clicked.connect(self.crop)
        self.reset_btn.clicked.connect(self.reset)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self.reject)
        self.canvas.selectionChanged.connect(self.update_controls)
        self.status = QLabel()
        hint = QLabel('Выделите мышью область для обрезки. Сохранение заменит исходное фото.')
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        for button in (self.flip_h_btn, self.flip_v_btn, self.crop_btn, self.reset_btn):
            tools.addWidget(button)
        tools.addStretch()
        advanced = QHBoxLayout()
        for widget in (QLabel("Пропорции"), self.aspect, self.rotate_left, self.rotate_right, self.undo_btn):
            advanced.addWidget(widget)
        advanced.addStretch()
        layout.addLayout(tools)
        layout.addLayout(advanced)
        layout.addWidget(hint)
        layout.addWidget(self.canvas, 1)
        footer = QHBoxLayout()
        footer.addWidget(self.status)
        footer.addStretch()
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.save_btn)
        layout.addLayout(footer)
        self.update_controls()

    def update_controls(self):
        selection = self.canvas.selection
        selected = not selection.isEmpty()
        self.undo_btn.setEnabled(bool(self.history))
        self.crop_btn.setEnabled(selected)
        self.save_btn.setEnabled(self.dirty or selected)
        self.reset_btn.setEnabled(self.dirty or selected)
        size = selection.size() if selected else self.canvas.image.size()
        self.status.setText(('Выделено: ' if selected else 'Размер: ') + f'{size.width()} × {size.height()} px')

    def remember(self):
        self.history.append(QImage(self.canvas.image))
        # Bound memory retained for full-resolution images.
        while len(self.history) > 1 and sum(image.sizeInBytes() for image in self.history) > 256*1024*1024:
            self.history.pop(0)

    def undo(self):
        if self.history:
            self.canvas.image = self.history.pop()
            self.dirty = self.canvas.image != self.original
            self.canvas.clear_selection()

    def rotate(self, angle):
        self.remember()
        self.canvas.image = self.canvas.image.transformed(QTransform().rotate(angle))
        self.dirty = True
        self.canvas.clear_selection()

    def flip(self, horizontal):
        self.remember()
        self.canvas.image = self.canvas.image.flipped(Qt.Horizontal if horizontal else Qt.Vertical)
        self.dirty = True
        self.canvas.clear_selection()

    def crop(self):
        if self.canvas.selection.isEmpty():
            return
        self.remember()
        self.canvas.image = self.canvas.image.copy(self.canvas.selection)
        self.dirty = True
        self.canvas.clear_selection()

    def reset(self):
        self.remember()
        self.canvas.image = QImage(self.original)
        self.dirty = False
        self.canvas.clear_selection()

    def save(self):
        if not self.dirty and self.canvas.selection.isEmpty():
            return
        self.crop()
        try:
            replace_photo(self.path, self.canvas.image, self.format, self.expected)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Не удалось сохранить фото', str(exc))
            return
        self.dirty = False
        self.accept()

    def reject(self):
        if self.dirty or not self.canvas.selection.isEmpty():
            answer = QMessageBox.question(self, 'Несохранённые правки', 'Закрыть редактор без сохранения?',
                                          QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                return
        super().reject()
