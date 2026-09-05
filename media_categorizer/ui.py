"""Shared Lucide controls and application-wide light/dark appearance."""
import json
from pathlib import Path
from xml.sax.saxutils import quoteattr
from PySide6.QtCore import Qt, QSize, QRectF, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap, QFont
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QPushButton

NODES = json.loads((Path(__file__).parent / 'assets/icons.json').read_text())
COLORS = {
    'dark': dict(bg='#11151d', panel='#1b2230', hover='#283449', text='#edf2fa', muted='#9aaac1', border='#344158', accent='#739cff', selected='#293f68', canvas='#0b1018'),
    'light': dict(bg='#f1f4f9', panel='#ffffff', hover='#e9effa', text='#202d43', muted='#60718b', border='#ccd6e5', accent='#315ed1', selected='#dfe9ff', canvas='#e4eaf3'),
}

def icon(name, color):
    body = ''.join('<' + tag + ' ' + ' '.join(k + '=' + quoteattr(str(v)) for k, v in attrs.items() if k != 'key') + '/>' for tag, attrs in NODES[name])
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="' + color + '" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>').encode()
    pix = QPixmap(48, 48)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    QSvgRenderer(svg).render(painter)
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)

class IconButton(QPushButton):
    def __init__(self, name, label, parent=None, compact=False):
        super().__init__('' if compact else label, parent)
        self.icon_name = name
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(20, 20))
        if compact:
            self.setFixedSize(38, 38)
        self.refresh_icon()

    def refresh_icon(self):
        theme = QApplication.instance().property('theme') or 'dark'
        self.setIcon(icon(self.icon_name, COLORS[theme]['text']))

    def set_playing(self, playing):
        self.icon_name = 'pause' if playing else 'play'
        self.setToolTip('Пауза (Пробел)' if playing else 'Воспроизвести (Пробел)')
        self.setAccessibleName(self.toolTip())
        self.refresh_icon()

class ToggleSwitch(QPushButton):
    """Keyboard-accessible checkable button with a custom painted switch."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.toggled.connect(self.update)

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_Space:
            event.accept()
            return True
        return super().event(event)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 66, 38)

    def paintEvent(self, event):
        c = COLORS[QApplication.instance().property('theme') or 'dark']
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(.4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(c['accent'] if self.isChecked() else c['border']))
        painter.drawRoundedRect(QRectF(4, (self.height()-22)/2, 38, 22), 11, 11)
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(QRectF(24 if self.isChecked() else 8, (self.height()-14)/2, 14, 14))
        painter.setPen(QColor(c['text']))
        painter.drawText(self.rect().adjusted(52, 0, -4, 0), Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setPen(QColor(c['accent']))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 6, 6)
        painter.end()

def apply_theme(theme):
    app = QApplication.instance()
    theme = theme if theme in COLORS else 'dark'
    app.setProperty('theme', theme)
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    c = COLORS[theme]
    palette = QPalette()
    for role, key in [(QPalette.Window,'bg'), (QPalette.WindowText,'text'), (QPalette.Base,'panel'), (QPalette.AlternateBase,'bg'), (QPalette.Text,'text'), (QPalette.Button,'panel'), (QPalette.ButtonText,'text'), (QPalette.Highlight,'accent'), (QPalette.HighlightedText,'panel'), (QPalette.ToolTipBase,'panel'), (QPalette.ToolTipText,'text')]:
        palette.setColor(role, QColor(c[key]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c['muted']))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c['muted']))
    app.setPalette(palette)
    app.setStyleSheet('''
        QWidget { color: %(text)s; font-family: "Segoe UI"; font-size: 13px; }
        QMainWindow, QDialog { background: %(bg)s; }
        QPushButton { background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px; padding: 8px 12px; }
        QPushButton:hover { background: %(hover)s; border-color: %(accent)s; }
        QPushButton:pressed, QPushButton:checked { background: %(selected)s; border-color: %(accent)s; }
        QPushButton:focus { border: 2px solid %(accent)s; }
        QPushButton:disabled { color: %(muted)s; background: %(bg)s; }
        QPushButton[primary="true"] { background: %(selected)s; border-color: %(accent)s; font-weight: 600; }
        QLabel#brand { font-size: 18px; font-weight: 650; }
        QLabel#muted { color: %(muted)s; }
        QGroupBox { background: %(panel)s; border: 1px solid %(border)s; border-radius: 10px; margin-top: 14px; padding: 16px 10px 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
        QComboBox, QLineEdit, QKeySequenceEdit { background: %(panel)s; border: 1px solid %(border)s; border-radius: 6px; padding: 7px; min-height: 20px; }
        QComboBox::drop-down { border: none; width: 20px; }
        QMenu { background: %(panel)s; border: 1px solid %(border)s; padding: 6px; }
        QMenu::item { padding: 9px 24px; border-radius: 5px; }
        QMenu::item:selected { background: %(selected)s; }
        QSlider::groove:horizontal { height: 5px; background: %(border)s; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
        QSlider::handle:horizontal { background: %(accent)s; border: 2px solid %(panel)s; width: 13px; margin: -6px 0; border-radius: 8px; }
        QScrollArea, QListWidget, QTableWidget { background: %(panel)s; border: 1px solid %(border)s; border-radius: 8px; }
        QListWidget::item:selected { background: %(selected)s; border: 1px solid %(accent)s; border-radius: 6px; }
        QHeaderView::section { background: %(panel)s; padding: 8px; border: none; border-bottom: 1px solid %(border)s; }
        QScrollBar:horizontal { background: %(bg)s; height: 10px; }
        QScrollBar:vertical { background: %(bg)s; width: 10px; }
        QScrollBar::handle { background: %(border)s; border-radius: 4px; min-width: 24px; min-height: 24px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QProgressBar { border: none; border-radius: 4px; background: %(border)s; text-align: center; max-height: 18px; }
        QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }
        QToolTip { background: %(panel)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px; }
    ''' % c)
    for widget in app.allWidgets():
        if isinstance(widget, IconButton):
            widget.refresh_icon()
        widget.update()
