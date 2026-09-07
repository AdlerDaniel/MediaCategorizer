"""Scene-based video framing: GPU video output with the same centered crop as export."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QTransform, QColor
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QApplication
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from .ui import COLORS


class VideoPreview(QGraphicsView):
    def __init__(self, info, parent=None):
        super().__init__(parent)
        self.info = info
        self.options = None
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.item = QGraphicsVideoItem()
        self.scene.addItem(self.item)
        self.item.nativeSizeChanged.connect(lambda _: self.update_geometry())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(240, 110)
        self.refresh_theme()

    def videoSink(self):
        return self.item.videoSink()

    def refresh_theme(self):
        self.setBackgroundBrush(QColor(COLORS[QApplication.instance().property('theme') or 'dark']['canvas']))

    def set_options(self, options):
        self.options = options
        self.update_geometry()

    def update_geometry(self):
        from PySide6.QtCore import QSizeF
        self.item.setSize(QSizeF(self.info['width'], self.info['height']))
        transform = QTransform()
        if self.options:
            # Scale before rotate in matrix composition = horizontal mirror in output space.
            transform.scale(-1 if self.options.mirror else 1, 1)
            transform.rotate(self.options.rotation)
        self.item.setTransform(transform)
        rect = self.item.sceneBoundingRect()
        if self.options and self.options.crop_ratio:
            width, height = self.options.geometry(self.info)
            rect = QRectF(rect.center().x()-width/2, rect.center().y()-height/2, width, height)
        self.setSceneRect(rect)
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.viewport().update()

    def drawForeground(self, painter, rect):
        from PySide6.QtGui import QPainterPath
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        outside = QPainterPath()
        outside.addRect(visible)
        inside = QPainterPath()
        inside.addRect(self.sceneRect())
        painter.fillPath(outside.subtracted(inside), self.backgroundBrush())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'item'):
            self.update_geometry()
