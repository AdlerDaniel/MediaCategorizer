"""Shared editor workspace, illustrated ratios and unobtrusive preview controls."""
from PySide6.QtCore import Qt, QSize, QRectF, Signal, QEvent, QTimer, QPropertyAnimation
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QRegion
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QGridLayout, QStackedWidget, QScrollArea, QButtonGroup, QApplication, QSizePolicy,
    QFrame, QGraphicsOpacityEffect, QDialog)
from .ui import IconButton, COLORS, ui_scale


class ElidedLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(), Qt.AlignVCenter | Qt.AlignLeft,
                         self.fontMetrics().elidedText(self.text(), Qt.ElideMiddle, self.contentsRect().width()))


class RatioCard(QPushButton):
    def __init__(self, label, ratio, parent=None):
        super().__init__(parent)
        self.label, self.ratio = label, ratio or 1.4
        self.setCheckable(True)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(68, 74)

    def sizeHint(self):
        return QSize(round(76*ui_scale()), round(80*ui_scale()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        painter.setOpacity(1 if self.isEnabled() else .45)
        painter.setBrush(QColor(c['selected'] if self.isChecked() else c['hover'] if self.underMouse() else c['panel']))
        painter.setPen(QPen(QColor(c['accent'] if self.isChecked() or self.hasFocus() else c['border']), 2 if self.isChecked() else 1))
        painter.drawRoundedRect(QRectF(self.rect().adjusted(2,2,-2,-2)), 9,9)
        w, h = min(34.,34*self.ratio), min(34.,34/self.ratio)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(c['accent'] if self.isChecked() else c['muted']),1.5))
        painter.drawRoundedRect(QRectF((self.width()-w)/2, 12, w, h), 2,2)
        painter.setPen(QColor(c['text']))
        painter.drawText(self.rect().adjusted(3,48,-3,-5), Qt.AlignCenter, self.label)
        painter.end()


class RatioCards(QWidget):
    def __init__(self, combo, parent=None):
        super().__init__(parent)
        self.combo = combo
        self.buttons = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0,0,0,0)
        grid.setSpacing(5)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i in range(combo.count()):
            value = combo.itemData(i)
            if isinstance(value, str) and ':' in value:
                a,b = map(float,value.split(':'))
                ratio = a/b
            else:
                ratio = value if isinstance(value,(int,float)) else None
            label = combo.itemText(i).replace('Квадрат ', '').replace('Без обрезки','Исходный')
            button = RatioCard(label, ratio)
            group.addButton(button, i)
            button.clicked.connect(lambda checked=False, index=i: combo.setCurrentIndex(index))
            grid.addWidget(button,i//3,i%3)
            self.buttons.append(button)
        combo.currentIndexChanged.connect(self.sync)
        self.sync()

    def sync(self):
        for i, button in enumerate(self.buttons):
            button.setChecked(i == self.combo.currentIndex())


class ToolPanel(QWidget):
    """Tab-compatible API with an icon rail and vertically scrolling settings."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0,0,0,0)
        self.rail = QVBoxLayout()
        self.rail.setContentsMargins(0,0,0,0)
        self.rail.addStretch()
        self.stack = QStackedWidget()
        self.stack.setObjectName('toolPanel')
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        row.addLayout(self.rail)
        row.addWidget(self.stack,1)
        self.refresh_theme()

    def refresh_theme(self):
        self.setFixedWidth(round(326*ui_scale()))

    def addTab(self, page, label):
        index = self.stack.count()
        symbols = {'Монтаж':'film', 'Обрезка':'crop', 'Кадрирование':'crop', 'Поворот':'rotate-cw', 'Горизонт':'rotate-ccw', 'Звук':'volume-2', 'Экспорт':'check'}
        button = IconButton(symbols.get(label,'settings-2'), label, compact=True)
        button.setCheckable(True)
        button.clicked.connect(lambda checked=False,i=index:self.setCurrentIndex(i))
        self.group.addButton(button,index)
        self.buttons.append(button)
        self.rail.insertWidget(index,button)
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        title = QLabel(label)
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        layout.addWidget(page)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(wrapper)
        self.stack.addWidget(scroll)
        if index == 0:
            button.setChecked(True)
        return index

    def setCurrentIndex(self,index):
        if 0 <= index < len(self.buttons):
            self.stack.setCurrentIndex(index)
            self.buttons[index].setChecked(True)
            self.currentChanged.emit(index)

    def currentIndex(self):
        return self.stack.currentIndex()


def field(layout, label, widget):
    title = QLabel(label)
    title.setWordWrap(True)
    title.setObjectName('muted')
    layout.addWidget(title)
    layout.addWidget(widget)


class PreviewHost(QWidget):
    def __init__(self, preview, controls=(), parent=None, floating=True):
        super().__init__(parent)
        self.floating = floating
        self.preview = preview
        self.fullscreen = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(preview,1)
        self.bar = QFrame(self)
        self.bar.setObjectName('floatingBar' if floating else 'editorBar')
        self.bar.setAttribute(Qt.WA_TranslucentBackground, floating)
        if not floating:
            self.bar.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
            layout.addWidget(self.bar)
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(9,6,9,6)
        row.setSpacing(5)
        if not floating: row.addStretch()
        for widget in controls:
            if not floating: widget.setSizePolicy(QSizePolicy.Maximum,QSizePolicy.Fixed)
            row.addWidget(widget)
        self.full_button = IconButton('maximize','Полный экран предпросмотра',compact=True)
        self.full_button.clicked.connect(self.toggle_fullscreen)
        row.addWidget(self.full_button)
        if not floating: row.addStretch()
        self.effect = QGraphicsOpacityEffect(self.bar)
        self.bar.setGraphicsEffect(self.effect)
        self.animation = QPropertyAnimation(self.effect,b'opacity',self)
        self.animation.setDuration(220)
        self.animation.finished.connect(self.finish_fade)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(2200)
        self.timer.timeout.connect(self.fade)
        for widget in [self,self.bar,preview,*self.bar.findChildren(QWidget),*preview.findChildren(QWidget)]:
            widget.installEventFilter(self)
            widget.setMouseTracking(True)
        self.reveal()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.MouseMove,QEvent.Enter,QEvent.FocusIn,QEvent.KeyPress) and isinstance(watched,QWidget) and (watched is self or self.isAncestorOf(watched)):
            self.reveal()
        return False

    def reveal(self):
        self.animation.stop()
        self.effect.setOpacity(.5 if self.floating else 1.)
        self.bar.show()
        self.bar.raise_()
        self.position_bar()
        self.timer.start()

    def fade(self):
        if not self.floating:
            return
        focused = QApplication.focusWidget()
        if self.bar.underMouse() or focused and self.bar.isAncestorOf(focused):
            self.timer.start()
            return
        self.animation.setStartValue(self.effect.opacity())
        self.animation.setEndValue(0.)
        self.animation.start()

    def finish_fade(self):
        if not self.floating:
            return
        if self.effect.opacity() == 0:
            self.bar.hide()

    def position_bar(self):
        if not self.floating:
            return
        self.bar.adjustSize()
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(self.bar.rect()),12,12)
        self.bar.setMask(QRegion(outline.toFillPolygon().toPolygon()))
        self.bar.move(max(0,(self.width()-self.bar.width())//2),max(0,self.height()-self.bar.height()-12))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.position_bar()

    def refresh_theme(self):
        if hasattr(self,'bar'):
            QTimer.singleShot(0,self,self.position_bar)

    def toggle_fullscreen(self):
        if self.fullscreen:
            self.fullscreen.reject()
            return
        host = self
        original_parent = self.parentWidget()
        original_layout = original_parent.layout()
        index = original_layout.indexOf(self)
        class Fullscreen(QDialog):
            def reject(dialog):
                dialog.layout().removeWidget(host)
                host.setParent(original_parent)
                original_layout.insertWidget(index,host,1)
                host.fullscreen = None
                host.show()
                super().reject()
                dialog.deleteLater()
        dialog = Fullscreen(self.window())
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setWindowTitle('Предпросмотр · Esc — выйти')
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self)
        self.fullscreen = dialog
        dialog.showFullScreen()
        self.reveal()
