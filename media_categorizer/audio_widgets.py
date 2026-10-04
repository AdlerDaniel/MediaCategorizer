"""Theme-aware audio waveform and compact circular processing indicator."""
from PySide6.QtCore import Qt, QRectF, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget
from .ui import COLORS, ui_scale


class WaveformWidget(QWidget):
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.peaks = ()
        self.duration = 0.
        self.position = 0.
        self.message = 'Загрузка аудиограммы…'
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('Аудиограмма видео — нажмите для перемотки')
        self.refresh_theme()

    def sizeHint(self):
        return QSize(400, round(62 * ui_scale()))

    def refresh_theme(self):
        self.setFixedHeight(round(62 * ui_scale()))
        self.update()

    def set_loading(self):
        self.peaks, self.duration, self.position = (), 0., 0.
        self.message = 'Загрузка аудиограммы…'
        self.setToolTip(self.message)
        self.update()

    def set_data(self, peaks, duration):
        self.peaks = tuple(peaks)
        self.duration = max(0., float(duration))
        self.message = '' if peaks else 'В видео нет звуковой дорожки'
        self.setToolTip('Громкость звука по времени · нажмите или перетащите для перемотки' if peaks else self.message)
        self.update()

    def set_error(self, error):
        self.message = 'Не удалось построить аудиограмму'
        self.setToolTip(error)
        self.update()

    def set_position(self, seconds):
        self.position = max(0., float(seconds))
        self.update()

    def waveform_rect(self):
        return QRectF(self.rect()).adjusted(9, 6, -9, -6)

    def _seek(self, x):
        rect = self.waveform_rect()
        if self.duration > 0 and rect.width() > 0:
            ratio = max(0., min(1., (x - rect.left()) / rect.width()))
            self.set_position(self.duration * ratio)
            self.seekRequested.emit(self.position)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._seek(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._seek(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            self._seek(self.waveform_rect().left() + self.waveform_rect().width() *
                       max(0., min(1., (self.position + (-1 if event.key() == Qt.Key_Left else 1)) / max(.001, self.duration))))
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c['panel']))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        rect = self.waveform_rect()
        center = rect.center().y()
        p.setPen(QColor(c['border']))
        p.drawLine(rect.left(), center, rect.right(), center)
        if self.peaks:
            bars = max(1, int(rect.width() / 3))
            for i in range(bars):
                left = i * len(self.peaks) // bars
                right = max(left + 1, (i + 1) * len(self.peaks) // bars)
                peak = max(self.peaks[left:right] or (0.,))
                height = max(.65, peak * rect.height() / 2)
                x = rect.left() + (i + .5) * rect.width() / bars
                color = QColor(c['accent'])
                color.setAlpha(230 if (i + .5) / bars <= self.position / max(.001, self.duration) else 125)
                p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(x, center - height, x, center + height)
        if self.message:
            p.setPen(QColor(c['muted']))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
        if self.duration > 0:
            x = rect.left() + min(1., self.position / self.duration) * rect.width()
            p.setPen(QPen(QColor(c['text']), 1.5))
            p.drawLine(x, rect.top(), x, rect.bottom())
        if self.hasFocus():
            p.setPen(QPen(QColor(c['accent']), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        p.end()


class RenderProgress(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value, self.angle = 0, 0
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.setAccessibleName('Удаление звука — прогресс обработки')
        self.refresh_theme()

    def refresh_theme(self):
        self.setFixedSize(round(42 * ui_scale()), round(42 * ui_scale()))
        self.update()

    def _tick(self):
        self.angle = (self.angle + 12) % 360
        self.update()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def set_value(self, value):
        self.value = max(0, min(100, int(value)))
        self.setToolTip(f'Удаление звука: {self.value}%')
        self.update()

    def paintEvent(self, event):
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor(c['border']), 3))
        p.drawEllipse(rect)
        p.setPen(QPen(QColor(c['accent']), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, (90 - self.angle if not self.value else 90) * 16,
                  -max(60, round(self.value * 3.6)) * 16)
        p.setPen(QColor(c['text']))
        p.drawText(self.rect(), Qt.AlignCenter, str(self.value) + '%')
        p.end()
