"""Range timeline with asynchronously decoded thumbnails and audio waveform."""
import math
import subprocess
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QThread, QRectF, QPointF
from PySide6.QtGui import QImage, QColor, QPainter, QPen, QBrush, QPolygonF
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
        self.setMinimumHeight(152)
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

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        area = QRectF(12,30,self.width()-24,self.height()-56)
        painter.fillRect(self.rect(),QColor(c['panel']))
        painter.fillRect(area,QColor(c['canvas']))
        # Choose human-friendly intervals from visible duration and label width.
        target = self.duration/self.zoom/max(1,area.width()/95)
        power = 10**math.floor(math.log10(max(.001,target)))
        step = next(v*power for v in (1,2,5,10) if v*power >= target)
        value = math.ceil(self.offset/step)*step
        painter.setPen(QColor(c['muted']))
        while value <= self.offset+self.duration/self.zoom+.00001:
            x = self.x(value)
            painter.drawLine(QPointF(x,22),QPointF(x,29))
            label = f'{int(value//60):02}:{value%60:06.3f}'
            painter.drawText(QRectF(max(0,min(self.width()-86,x-43)),2,86,18),Qt.AlignCenter,label)
            for fraction in (.25,.5,.75):
                minor = self.x(value+step*fraction)
                if 12 <= minor <= self.width()-12:
                    painter.drawLine(QPointF(minor,26),QPointF(minor,29))
            value += step
        painter.save()
        painter.setClipRect(area)
        thumb_height = area.height()*.56
        if self.images:
            width = area.width()*self.zoom/len(self.images)
            for index,image in enumerate(self.images):
                painter.drawImage(QRectF(self.x(0)+index*width,30,width,thumb_height),image)
        if not self.waveform.isNull():
            wave = self.waveform.copy()
            tint = QPainter(wave)
            tint.setCompositionMode(QPainter.CompositionMode_SourceIn)
            tint.fillRect(wave.rect(),QColor(c['accent']))
            tint.end()
            painter.drawImage(QRectF(self.x(0),32+thumb_height,area.width()*self.zoom,area.height()-thumb_height-2),wave)
        left,right = self.x(self.start),self.x(self.end)
        selection = QColor(c['accent'])
        selection.setAlpha(35)
        painter.fillRect(QRectF(left,30,right-left,area.height()),selection)
        for a,b in self.cuts:
            cut = QRectF(self.x(a),30,self.x(b)-self.x(a),area.height())
            painter.fillRect(cut,QColor(c['panel']))
            painter.fillRect(cut,QBrush(QColor(c['muted']),Qt.BDiagPattern))
            if cut.width()>90:
                label_rect = QRectF(max(12,cut.left())+5,area.center().y()-12,min(cut.width()-10,140),24)
                painter.fillRect(label_rect,QColor(c['panel']))
                painter.setPen(QColor(c['text']))
                painter.drawText(label_rect,Qt.AlignCenter,'Вырезать')
        painter.fillRect(QRectF(12,30,max(0,left-12),area.height()),QColor(0,0,0,160))
        painter.fillRect(QRectF(right,30,max(0,self.width()-12-right),area.height()),QColor(0,0,0,160))
        painter.setPen(QPen(QColor(c['accent']),2))
        painter.drawRect(QRectF(left,30,right-left,area.height()))
        painter.restore()
        for x in (left,right):
            if 12 <= x <= self.width()-12:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(c['accent']))
                painter.drawRoundedRect(QRectF(x-7,29,14,area.height()+2),4,4)
                painter.setPen(QPen(QColor(c['bg']),2))
                painter.drawLine(QPointF(x-2,area.center().y()-7),QPointF(x-2,area.center().y()+7))
                painter.drawLine(QPointF(x+2,area.center().y()-7),QPointF(x+2,area.center().y()+7))
        x = self.x(self.position)
        if 12 <= x <= self.width()-12:
            painter.setPen(QPen(QColor(c['text']),2))
            painter.drawLine(QPointF(x,25),QPointF(x,area.bottom()+3))
            painter.setBrush(QColor(c['text']))
            painter.drawPolygon(QPolygonF([QPointF(x-5,20),QPointF(x+5,20),QPointF(x,27)]))
        painter.setPen(QColor(c['muted']))
        painter.drawText(12,self.height()-6,f'Начало {self.start:.3f} с')
        end_label = f'Конец {self.end:.3f} с'
        painter.drawText(self.width()-12-self.fontMetrics().horizontalAdvance(end_label),self.height()-6,end_label)
        painter.end()
