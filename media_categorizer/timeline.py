"""Range timeline with asynchronously decoded thumbnails and audio waveform."""
import subprocess
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QThread, QRectF
from PySide6.QtGui import QImage, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QApplication
from .ui import COLORS
from .video_export import tool, CREATE_FLAGS

class TimelineAssets(QThread):
    def __init__(self, path, duration, audio, parent=None):
        super().__init__(parent)
        self.path, self.duration, self.audio = path, duration, audio
        self.images, self.waveform = [], QImage()
        self.process = None

    def stop(self):
        self.requestInterruption()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass

    def command(self, args):
        if self.isInterruptionRequested():
            return False
        self.process = subprocess.Popen([tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_FLAGS)
        if self.isInterruptionRequested():
            self.stop()
        try:
            return self.process.wait(timeout=120) == 0
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            return False

    def run(self):
        try:
            with tempfile.TemporaryDirectory(prefix='mc-timeline-') as folder:
                directory = Path(folder)
                self.command(['-i', str(self.path), '-an', '-vf', f'fps={8/max(.001,self.duration)},scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2', '-frames:v', '8', str(directory/'thumb-%02d.jpg')])
                self.images = [QImage(str(p)) for p in sorted(directory.glob('thumb-*.jpg'))]
                if self.audio and not self.isInterruptionRequested():
                    width = max(1, min(1200, int(self.duration * 4000)))
                    self.command(['-i', str(self.path), '-filter_complex', f'[0:a:0]aformat=channel_layouts=mono,aresample=8000,showwavespic=s={width}x64:colors=0x739cff[wave]', '-map', '[wave]', '-frames:v', '1', str(directory/'wave.png')])
                    self.waveform = QImage(str(directory/'wave.png'))
        except (OSError, ValueError):
            pass

class RangeTimeline(QWidget):
    rangeChanged = Signal(float, float)
    seekRequested = Signal(int)
    viewChanged = Signal()

    def __init__(self, duration, parent=None):
        super().__init__(parent)
        self.duration = max(.001, duration)
        self.start, self.end, self.position = 0., self.duration, 0.
        self.images, self.waveform = [], QImage()
        self.mode = None
        self.zoom = 1.
        self.offset = 0.
        self.frame_rate = 30.
        self.cuts = ()
        self.setMinimumHeight(120)
        self.setMinimumWidth(320)
        self.setAccessibleName('Диапазон обрезки видео; точные границы доступны в полях начала и конца')
        self.setToolTip('Перетащите левую или правую границу. Щелчок внутри шкалы — переход к кадру.')

    def x(self, seconds):
        return 12+(self.width()-24)*(seconds-self.offset)/(self.duration/self.zoom)

    def seconds(self, x):
        value = max(0, min(self.duration, self.offset+(x-12)/max(1,self.width()-24)*self.duration/self.zoom))
        return min(self.duration, round(value*self.frame_rate)/self.frame_rate)

    def set_zoom(self, zoom, anchor=None):
        anchor = self.position if anchor is None else anchor
        fraction = (anchor-self.offset)/(self.duration/self.zoom)
        self.zoom = max(1., min(64., zoom))
        self.offset = max(0., min(self.duration-self.duration/self.zoom, anchor-fraction*self.duration/self.zoom))
        self.update()
        self.viewChanged.emit()

    def set_offset(self, fraction):
        self.offset = fraction*max(0, self.duration-self.duration/self.zoom)
        self.update()

    def wheelEvent(self, event):
        self.set_zoom(self.zoom*(1.5 if event.angleDelta().y() > 0 else 1/1.5), self.seconds(event.position().x()))
        event.accept()

    def set_range(self, start, end):
        self.start, self.end = start, end
        self.update()

    def set_position(self, value):
        self.position = value/1000
        if not self.offset <= self.position <= self.offset+self.duration/self.zoom:
            self.offset = max(0, min(self.duration-self.duration/self.zoom, self.position-self.duration/self.zoom/2))
            self.viewChanged.emit()
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        if abs(x-self.x(self.start)) <= 12:
            self.mode = 'start'
        elif abs(x-self.x(self.end)) <= 12:
            self.mode = 'end'
        else:
            self.mode = 'seek'
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if not self.mode:
            return
        seconds = self.seconds(event.position().x())
        if self.mode == 'start':
            self.start = min(seconds, max(0,self.end-1/self.frame_rate))
        elif self.mode == 'end':
            self.end = max(seconds, min(self.duration,self.start+1/self.frame_rate))
        else:
            self.seekRequested.emit(round(seconds*1000))
            return
        self.rangeChanged.emit(self.start, self.end)
        self.update()

    def mouseReleaseEvent(self, event):
        self.mouseMoveEvent(event)
        self.mode = None

    def paintEvent(self, event):
        painter = QPainter(self)
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        area = QRectF(12, 8, self.width()-24, self.height()-32)
        bottom, height = area.bottom(), area.height()
        thumb_height = min(64., height*.55)
        painter.setClipRect(area.adjusted(-5, -3, 5, 4))
        painter.fillRect(area, QColor(c['canvas']))
        if self.images:
            width = area.width()*self.zoom/len(self.images)
            for index, image in enumerate(self.images):
                painter.drawImage(QRectF(self.x(0)+index*width, 8, width, thumb_height), image)
        if not self.waveform.isNull():
            wave = self.waveform.copy()
            tint = QPainter(wave)
            tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
            tint.fillRect(wave.rect(), QColor(c['accent']))
            tint.end()
            painter.drawImage(QRectF(self.x(0), 10+thumb_height, area.width()*self.zoom, max(1,height-thumb_height-2)), wave)
        for a, b in self.cuts:
            cut = QRectF(self.x(a), 8, self.x(b)-self.x(a), height)
            painter.fillRect(cut, QColor(0, 0, 0, 195))
            painter.setPen(QPen(QColor(c['muted']), 1, Qt.DashLine))
            painter.drawRect(cut)
            if cut.width() > 60:
                painter.drawText(cut, Qt.AlignCenter, 'Удалить')
        left, right = self.x(self.start), self.x(self.end)
        painter.fillRect(QRectF(12,8,max(0,left-12),height), QColor(0,0,0,145))
        painter.fillRect(QRectF(right,8,max(0,self.width()-12-right),height), QColor(0,0,0,145))
        painter.setPen(QPen(QColor(c['accent']), 2))
        painter.drawRect(QRectF(left,8,max(0,right-left),height))
        for x in (left,right):
            painter.fillRect(QRectF(x-4,8,8,height), QColor(c['accent']))
        painter.setPen(QPen(QColor(c['text']), 2))
        painter.drawLine(round(self.x(self.position)), 6, round(self.x(self.position)), round(bottom+2))
        painter.setClipping(False)
        painter.setPen(QColor(c['text']))
        painter.drawText(12,self.height()-6,f'{self.offset:.3f} с')
        painter.drawText(self.width()-110,self.height()-6,f'{self.offset+self.duration/self.zoom:.3f} с')
        painter.end()
