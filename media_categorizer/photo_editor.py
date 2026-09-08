"""Photo editing with full-resolution crops and atomic replacement of the original."""
import hashlib
import math
from pathlib import Path
from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QSaveFile, QIODevice, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QImageWriter, QPainter, QPen, QTransform
from PySide6.QtWidgets import QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QComboBox, QSpinBox, QDoubleSpinBox, QAbstractSpinBox, QGridLayout
from .ui import IconButton, COLORS
from .ui import IntegerSpinBox as QSpinBox, DecimalSpinBox as QDoubleSpinBox
from .editor_widgets import ToolPanel, RatioCards, PreviewHost, ElidedLabel, field
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
    zoomChanged = Signal(float)

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = image
        self.selection = QRect()
        self.start = None
        self.ratio = None
        self.drag_mode = "new"
        self.drag_rect = QRect()
        self.zoom = 1.
        self.pan = QPointF()
        self.pan_start = None
        self.guides = 'thirds'
        self.horizon = 0.
        self.horizon_visible = False
        self.crop_enabled = True
        self.setMinimumSize(320, 220)
        self.setCursor(Qt.CrossCursor)
        self.setAccessibleName('Область обрезки фотографии')

    def image_rect(self):
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())*self.zoom
        width, height = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width()-width)/2+self.pan.x(), (self.height()-height)/2+self.pan.y(), width, height)

    def set_zoom(self, value):
        self.zoom = max(1., min(16., value))
        if self.zoom == 1:
            self.pan = QPointF()
        self.update()
        self.zoomChanged.emit(self.zoom)

    def set_guides(self, value):
        self.guides = value
        self.update()

    def set_horizon(self, value):
        self.horizon = value
        self.update()

    def set_horizon_visible(self, value):
        self.horizon_visible = value
        self.crop_enabled = not value
        self.update()

    def wheelEvent(self, event):
        self.set_zoom(self.zoom*(1.25 if event.angleDelta().y() > 0 else .8))
        event.accept()

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
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier):
            self.pan_start = event.position()
            event.accept()
            return
        if not self.crop_enabled or event.button() != Qt.LeftButton or not self.image_rect().contains(event.position()):
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
            for side, coordinate, value, low, high in (
                ('left',point.x(),r.x(),r.y(),r.y()+r.height()),
                ('right',point.x(),r.x()+r.width(),r.y(),r.y()+r.height()),
                ('top',point.y(),r.y(),r.x(),r.x()+r.width()),
                ('bottom',point.y(),r.y()+r.height(),r.x(),r.x()+r.width())):
                other = point.y() if side in ('left','right') else point.x()
                if abs(coordinate-value) <= threshold and low <= other <= high:
                    self.drag_mode = side
                    self.start = point
                    return
            if r.contains(point.toPoint()):
                self.drag_mode = 'move'
                self.start = point
                return
        self.clear_selection()
        self.start = point
        event.accept()

    def mouseMoveEvent(self, event):
        if self.pan_start is not None:
            self.pan += event.position()-self.pan_start
            self.pan_start = event.position()
            # Keep at least the image center reachable; Fit always resets panning.
            rect = self.image_rect()
            self.pan.setX(max(-rect.width()/2, min(rect.width()/2, self.pan.x())))
            self.pan.setY(max(-rect.height()/2, min(rect.height()/2, self.pan.y())))
            self.update()
            return
        if self.start is None:
            return
        end = self.source_point(event.position())
        if self.drag_mode in ('left','right','top','bottom'):
            r = self.drag_rect
            left,top,right,bottom = r.x(),r.y(),r.x()+r.width(),r.y()+r.height()
            if self.drag_mode == 'left': left = min(right-1,round(end.x()))
            if self.drag_mode == 'right': right = max(left+1,round(end.x()))
            if self.drag_mode == 'top': top = min(bottom-1,round(end.y()))
            if self.drag_mode == 'bottom': bottom = max(top+1,round(end.y()))
            if self.ratio:
                if self.drag_mode in ('left','right'):
                    height = min(self.image.height(),(right-left)/self.ratio)
                    width = height*self.ratio
                    left,right = (right-width,right) if self.drag_mode=='left' else (left,left+width)
                    top = max(0,min(self.image.height()-height,r.center().y()-height/2))
                    bottom = top+height
                else:
                    width = min(self.image.width(),(bottom-top)*self.ratio)
                    height = width/self.ratio
                    top,bottom = (bottom-height,bottom) if self.drag_mode=='top' else (top,top+height)
                    left = max(0,min(self.image.width()-width,r.center().x()-width/2))
                    right = left+width
            self.selection = QRect(round(left),round(top),round(right-left),round(bottom-top)).intersected(self.image.rect())
        elif self.drag_mode == 'move':
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
        if self.pan_start is not None:
            self.pan_start = None
            return
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
        if self.crop_enabled and not self.selection.isEmpty():
            scale = rect.width()/self.image.width()
            selected = QRectF(rect.x()+self.selection.x()*scale, rect.y()+self.selection.y()*scale,
                              self.selection.width()*scale, self.selection.height()*scale)
            painter.fillRect(rect, QColor(0, 0, 0, 130))
            painter.drawImage(selected, self.image, QRectF(self.selection))
            painter.setPen(QPen(QColor(c['accent']), 1.5))
            painter.drawRect(selected)
            for point in (selected.topLeft(), selected.topRight(), selected.bottomLeft(), selected.bottomRight(), QPointF(selected.center().x(),selected.top()), QPointF(selected.center().x(),selected.bottom()), QPointF(selected.left(),selected.center().y()), QPointF(selected.right(),selected.center().y())):
                painter.fillRect(QRectF(point.x()-4, point.y()-4, 8, 8), QColor(c['accent']))
            parts = {'thirds':(1/3,2/3),'center':(.5,),'dense':tuple(i/8 for i in range(1,8)),'none':()}[self.guides]
            for part in parts:
                painter.drawLine(QPointF(selected.x()+selected.width()*part, selected.top()), QPointF(selected.x()+selected.width()*part, selected.bottom()))
                painter.drawLine(QPointF(selected.left(), selected.y()+selected.height()*part), QPointF(selected.right(), selected.y()+selected.height()*part))
            label = f'{self.selection.width()} × {self.selection.height()} px'
            width = self.fontMetrics().horizontalAdvance(label)+20
            box = QRectF(max(4,min(self.width()-width-4,selected.center().x()-width/2)),max(4,min(self.height()-32,selected.bottom()-32)),width,26)
            painter.fillRect(box,QColor(c['panel']))
            painter.setPen(QColor(c['text']))
            painter.drawText(box,Qt.AlignCenter,label)
        if self.horizon_visible:
            painter.save()
            painter.translate(rect.center())
            painter.rotate(0)
            painter.setPen(QPen(QColor(c['accent']),2))
            length = min(self.width()*.35,rect.width()*.45)
            painter.drawLine(QPointF(-length,0),QPointF(length,0))
            painter.drawLine(QPointF(0,-10),QPointF(0,10))
            painter.restore()
            label = f'{self.horizon:+.2f}°'
            box = QRectF(rect.center().x()-45,rect.center().y()+16,90,28)
            painter.fillRect(box,QColor(c['panel']))
            painter.setPen(QColor(c['text']))
            painter.drawText(box,Qt.AlignCenter,label)
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
        self.crop_fields = []
        self.zoom_out = IconButton('minus', 'Уменьшить', compact=True)
        self.zoom_in = IconButton('plus', 'Увеличить', compact=True)
        self.zoom_fit = IconButton('maximize', 'Вписать')
        self.zoom_label = QLabel('100%')
        self.zoom_out.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom/1.25))
        self.zoom_in.clicked.connect(lambda: self.canvas.set_zoom(self.canvas.zoom*1.25))
        self.zoom_fit.clicked.connect(lambda: self.canvas.set_zoom(1))
        self.canvas.zoomChanged.connect(lambda value: self.zoom_label.setText(f'{value*100:.0f}%'))
        self.angle = QDoubleSpinBox()
        self.angle.setButtonSymbols(QAbstractSpinBox.PlusMinus)
        self.angle.setRange(-45,45)
        self.angle.setDecimals(2)
        self.angle.setSingleStep(.1)
        self.angle.setSuffix('°')
        self.angle.setAccessibleName('Угол выравнивания горизонта')
        self.horizon_source = None
        self.angle.valueChanged.connect(self.preview_horizon)
        self.straighten_btn = IconButton('rotate-cw','Выровнять')
        self.straighten_btn.clicked.connect(self.straighten)
        self.guides = QComboBox()
        for label, value in [('Трети','thirds'),('Центр','center'),('Частая сетка','dense'),('Без сетки','none')]:
            self.guides.addItem(label,value)
        self.guides.currentIndexChanged.connect(lambda: self.canvas.set_guides(self.guides.currentData()))
        self.tools = ToolPanel()
        crop_page, rotate_page, horizon_page = QWidget(), QWidget(), QWidget()
        crop_layout, rotate_layout, horizon_layout = QVBoxLayout(crop_page), QVBoxLayout(rotate_page), QVBoxLayout(horizon_page)
        self.aspect.setParent(self)
        self.aspect.hide()
        self.ratio_cards = RatioCards(self.aspect)
        crop_layout.addWidget(self.ratio_cards)
        coordinates = QGridLayout()
        for i,label in enumerate(('X','Y','Ширина','Высота')):
            spin = QSpinBox()
            spin.setButtonSymbols(QAbstractSpinBox.PlusMinus)
            spin.setRange(0,1000000)
            spin.setSuffix(' px')
            spin.setAccessibleName('Обрезка: '+label)
            spin.valueChanged.connect(self.exact_crop)
            coordinates.addWidget(QLabel(label),i,0)
            coordinates.addWidget(spin,i,1)
            self.crop_fields.append(spin)
        crop_layout.addLayout(coordinates)
        field(crop_layout,'Направляющие',self.guides)
        crop_layout.addWidget(self.crop_btn)
        for button in (self.flip_h_btn,self.flip_v_btn):
            rotate_layout.addWidget(button)
        rotations = QHBoxLayout()
        rotations.addWidget(self.rotate_left)
        rotations.addWidget(self.rotate_right)
        rotate_layout.addLayout(rotations)
        rotate_layout.addWidget(QLabel('Поворот на 90°'))
        field(horizon_layout,'Угол горизонта',self.angle)
        horizon_layout.addWidget(self.straighten_btn)
        note = QLabel('Изменяйте угол — фото сразу выравнивается. Линия остаётся горизонтальной. Пустые углы обрезаются.')
        note.setWordWrap(True)
        horizon_layout.addWidget(note)
        for page,label in ((crop_page,'Обрезка'),(rotate_page,'Поворот'),(horizon_page,'Горизонт')):
            self.tools.addTab(page,label)
        self.tools.currentChanged.connect(self.change_tool)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = ElidedLabel('Фото · '+self.path.name)
        title.setObjectName('sectionTitle')
        header.addWidget(title,1)
        header.addWidget(self.undo_btn)
        header.addWidget(self.reset_btn)
        self.reset_btn.setProperty('destructive',True)
        layout.addLayout(header)
        workspace = QHBoxLayout()
        self.preview_host = PreviewHost(self.canvas,(self.zoom_out,self.zoom_label,self.zoom_in,self.zoom_fit), floating=False)
        workspace.addWidget(self.preview_host,1)
        workspace.addWidget(self.tools)
        container = QWidget()
        container.setLayout(workspace)
        layout.addWidget(container,1)
        hint.setText('Колесо — масштаб · Ctrl + перетаскивание — перемещение · Сохранение заменит исходное фото')
        hint.setObjectName('muted')
        layout.addWidget(hint)
        footer = QHBoxLayout()
        footer.addWidget(self.status,1)
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
        if hasattr(self, 'crop_fields'):
            rect = selection if selected else self.canvas.image.rect()
            for spin in self.crop_fields:
                spin.blockSignals(True)
            w, h = self.canvas.image.width(), self.canvas.image.height()
            for spin, maximum, value in zip(self.crop_fields, (w-1, h-1, w-rect.x(), h-rect.y()), (rect.x(), rect.y(), rect.width(), rect.height())):
                spin.setMaximum(maximum)
                spin.setValue(value)
                spin.blockSignals(False)

    def exact_crop(self):
        if len(self.crop_fields) != 4:
            return
        x, y, width, height = [spin.value() for spin in self.crop_fields]
        width = max(1, min(width, self.canvas.image.width()-x))
        height = max(1, min(height, self.canvas.image.height()-y))
        self.aspect.blockSignals(True)
        self.aspect.setCurrentIndex(0)
        self.aspect.blockSignals(False)
        self.ratio_cards.sync()
        self.canvas.ratio = None
        self.canvas.selection = QRect(x, y, width, height)
        self.canvas.update()
        self.update_controls()

    def change_tool(self, index):
        if index != 2:
            self.straighten()
        self.canvas.clear_selection()
        self.canvas.set_horizon_visible(index == 2)

    def preview_horizon(self, angle):
        if self.horizon_source is None:
            self.horizon_source = QImage(self.canvas.image)
        source = self.horizon_source
        self.canvas.horizon = angle
        rotated = source.transformed(QTransform().rotate(angle), Qt.SmoothTransformation)
        radians = math.radians(abs(angle))
        w,h = source.width(),source.height()
        factor = min(w/(w*math.cos(radians)+h*math.sin(radians)),h/(w*math.sin(radians)+h*math.cos(radians)))
        cw,ch = max(1,int(w*factor)-2),max(1,int(h*factor)-2)
        self.canvas.image = source.copy() if not angle else rotated.copy((rotated.width()-cw)//2,(rotated.height()-ch)//2,cw,ch)
        self.canvas.clear_selection()
        self.save_btn.setEnabled(bool(angle) or self.dirty)
        self.reset_btn.setEnabled(bool(angle) or self.dirty)
        self.undo_btn.setEnabled(bool(angle) or bool(self.history))

    def straighten(self):
        angle = self.angle.value()
        if not angle:
            return
        source = self.horizon_source or self.canvas.image
        self.history.append(QImage(source))
        rotated = source.transformed(QTransform().rotate(angle), Qt.SmoothTransformation)
        # Largest centered rectangle of the original aspect entirely inside the rotated image.
        radians = math.radians(abs(angle))
        w, h = source.width(), source.height()
        factor = min(w/(w*math.cos(radians)+h*math.sin(radians)), h/(w*math.sin(radians)+h*math.cos(radians)))
        cw, ch = max(1, int(w*factor)-2), max(1, int(h*factor)-2)
        self.canvas.image = rotated.copy((rotated.width()-cw)//2, (rotated.height()-ch)//2, cw, ch)
        self.dirty = True
        self.canvas.set_zoom(1)
        self.canvas.clear_selection()
        self.horizon_source = None
        self.angle.blockSignals(True)
        self.angle.setValue(0)
        self.angle.blockSignals(False)
        self.canvas.horizon = 0

    def remember(self):
        self.history.append(QImage(self.canvas.image))
        # Bound memory retained for full-resolution images.
        while len(self.history) > 1 and sum(image.sizeInBytes() for image in self.history) > 256*1024*1024:
            self.history.pop(0)

    def undo(self):
        if self.horizon_source is not None:
            self.canvas.image = self.horizon_source
            self.horizon_source = None
            self.angle.blockSignals(True)
            self.angle.setValue(0)
            self.angle.blockSignals(False)
            self.canvas.horizon = 0
            self.canvas.clear_selection()
            return
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
        self.horizon_source = None
        self.angle.blockSignals(True)
        self.angle.setValue(0)
        self.angle.blockSignals(False)
        self.canvas.horizon = 0
        self.remember()
        self.canvas.image = QImage(self.original)
        self.dirty = False
        self.canvas.clear_selection()

    def save(self):
        self.straighten()
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
        if self.dirty or self.angle.value() or not self.canvas.selection.isEmpty():
            answer = QMessageBox.question(self, 'Несохранённые правки', 'Закрыть редактор без сохранения?',
                                          QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                return
        super().reject()
