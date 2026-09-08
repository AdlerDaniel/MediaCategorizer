from PySide6.QtCore import QEvent, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QImage, QPainter, QWindow, QTransform
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QFrame, QWidget, QSizePolicy, QGraphicsView, QGraphicsScene
from .view_geometry import PanZoom
from .ui import COLORS
from PySide6.QtGui import QColor


class PanZoomInput:
    def _init_interaction(self):
        self.view = PanZoom()
        self._drag_position = None
        self._wheel_remainder = 0.0
        self.setToolTip("Колесо — масштаб под курсором; левая кнопка — перемещение увеличенного медиа")

    def _area_and_source(self):
        raise NotImplementedError

    def _refresh_view(self):
        raise NotImplementedError

    def _anchor_position(self, event):
        return event.position().x(), event.position().y()

    def set_zoom_percent(self, percent, anchor=None):
        area, source = self._area_and_source()
        self.view.zoom(percent, area, source, anchor)
        self._refresh_view()
        self._update_pan_cursor()

    def reset_pan(self):
        self._drag_position = None
        self.view.reset_pan()
        self._refresh_view()
        self._update_pan_cursor()

    def _update_pan_cursor(self):
        self.setCursor(Qt.ClosedHandCursor if self._drag_position is not None else
                       Qt.OpenHandCursor if self.view.percent > 100 else Qt.ArrowCursor)

    def _handle_wheel(self, event, anchor):
        delta = event.angleDelta().y()
        if not delta:
            delta = event.pixelDelta().y() * 3
        if not delta:
            event.ignore()
            return
        self._wheel_remainder += delta
        steps = int(self._wheel_remainder / 120)
        self._wheel_remainder -= steps * 120
        if steps:
            self.set_zoom_percent(self.view.percent + steps * 10, anchor)
            self.zoomChanged.emit(self.view.percent)
        event.accept()

    def wheelEvent(self, event):
        self._handle_wheel(event, self._anchor_position(event))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.view.percent > 100:
            self._drag_position = event.globalPosition()
            self._update_pan_cursor()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_position is not None:
            position = event.globalPosition()
            delta = position - self._drag_position
            self._drag_position = position
            area, source = self._area_and_source()
            self.view.pan(delta.x(), delta.y(), area, source)
            self._refresh_view()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_position is not None:
            self._drag_position = None
            self._update_pan_cursor()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


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


class VideoViewport(QAbstractScrollArea):
    """A native clipping boundary for Qt's embedded video window.

    QWindowContainer recognizes QAbstractScrollArea ancestors and creates the
    native parent chain needed to keep enlarged video off surrounding controls.
    """
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class ImageCanvas(PanZoomInput, QWidget):
    zoomChanged = Signal(int)

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
        self._init_interaction()
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

    def _area_and_source(self):
        area = self.contentsRect()
        return (area.width(), area.height()), (self._image.width(), self._image.height())

    def _refresh_view(self):
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS[QApplication.instance().property("theme") or "dark"]["canvas"]))
        painter.setPen(self.palette().windowText().color())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if self._image.isNull():
            if self._message:
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
            painter.end()
            return

        area, source = self._area_and_source()
        x, y, width, height = self.view.rectangle(*area, *source)
        target = QRectF(x, y, width, height)
        painter.drawImage(target, self._image)
        painter.end()


class VideoCanvas(PanZoomInput, QGraphicsView):
    zoomChanged = Signal(int)
    pointerMoved = Signal()

    """Native Qt video surface with adaptive geometry.

    QVideoWidget keeps the decoded frame in Qt's video pipeline instead of
    converting every 4K/60fps frame to QImage in Python.  This is the main
    playback-performance change in V4.2.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._video_item = QGraphicsVideoItem()
        self._scene.addItem(self._video_item)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._source_size = QSize(16, 9)
        self._auto_rotation = 0
        self._manual_rotation = 0
        self._frame_rotation = 0
        self._init_interaction()
        self.setMinimumSize(1, 1)
        self._video_item.setAspectRatioMode(Qt.IgnoreAspectRatio)
        QApplication.instance().installEventFilter(self)
        if parent is not None:
            parent.installEventFilter(self)

    def videoSink(self):
        return self._video_item.videoSink()

    def mouseMoveEvent(self,event):
        self.pointerMoved.emit()
        super().mouseMoveEvent(event)

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

    def set_video_geometry(self, size: QSize, auto_rotation=0, frame_rotation=None):
        changed = False
        frame_rotation = int(auto_rotation if frame_rotation is None else frame_rotation) % 360
        if frame_rotation != self._frame_rotation:
            self._frame_rotation = frame_rotation
            changed = True
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

    def _area_and_source(self):
        parent = self.parentWidget()
        area = parent.contentsRect().size() if parent is not None else self.size()
        source = self._effective_size()
        return (area.width(), area.height()), (source.width(), source.height())

    def _refresh_view(self):
        self.fit_to_parent()

    def _anchor_position(self, event):
        point = self.mapToParent(event.position().toPoint())
        return point.x(), point.y()

    def _owns_native_window(self, watched):
        # Qt 6.11 embeds a QVideoWindow whose QObject parent is the top-level
        # window, not this widget. Native mouse events do not bubble to QWidget.
        if not isinstance(watched, QWindow) or watched.metaObject().className() != "QVideoWindow":
            return False
        parent = watched.parent()
        top = self.window().windowHandle()
        while isinstance(parent, QWindow):
            if parent is top:
                return True
            parent = parent.parent()
        return False

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type not in (QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease):
            return super().eventFilter(watched, event)
        if watched is self.parentWidget() and event_type == QEvent.Wheel:
            self._handle_wheel(event, (event.position().x(), event.position().y()))
            return True
        if self.isVisible() and self._owns_native_window(watched):
            if event_type == QEvent.MouseMove:
                self.pointerMoved.emit()
            if event_type == QEvent.Wheel:
                point = self.parentWidget().mapFromGlobal(event.globalPosition().toPoint())
                self._handle_wheel(event, (point.x(), point.y()))
            elif event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and self.view.percent > 100:
                self.mousePressEvent(event)
                watched.setMouseGrabEnabled(True)
            elif event_type == QEvent.MouseMove and self._drag_position is not None:
                self.mouseMoveEvent(event)
            elif event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._drag_position is not None:
                self.mouseReleaseEvent(event)
                watched.setMouseGrabEnabled(False)
            else:
                return False
            watched.setCursor(self.cursor())
            return True
        return super().eventFilter(watched, event)

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

        area, source = self._area_and_source()
        x, y, width, height = self.view.rectangle(*area, *source)
        self.setGeometry(round(x), round(y), max(1, round(width)), max(1, round(height)))
        source=QSize(self._source_size)
        if self._frame_rotation % 180:source.transpose()
        self._video_item.setSize(QSizeF(source))
        self._video_item.setTransform(QTransform().rotate(self._manual_rotation+self._auto_rotation-self._frame_rotation))
        self.setSceneRect(self._video_item.sceneBoundingRect())
        self.fitInView(self.sceneRect(),Qt.IgnoreAspectRatio)
