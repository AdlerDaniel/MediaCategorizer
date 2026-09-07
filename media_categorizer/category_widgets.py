from PySide6.QtCore import Qt, QMimeData, Signal, QEvent
from PySide6.QtGui import QDrag, QColor
from PySide6.QtWidgets import QWidget, QApplication, QScrollArea
from .ui import IconButton, COLORS

MIME = 'application/x-mediacategorizer-category'


class CategoryScrollArea(QScrollArea):
    def focusNextPrevChild(self, next):
        # QScrollArea calls ensureWidgetVisible even when disabling a button
        # transfers focus automatically. Preserve position for that transfer.
        focused = QApplication.focusWidget()
        if focused is not None and not focused.isEnabled():
            return QWidget.focusNextPrevChild(self, next)
        return super().focusNextPrevChild(next)

class CategoryButton(IconButton):
    def __init__(self, category, action_label, parent=None):
        name = category['name']
        shortcut = category.get('shortcut', '')
        label = name + (f'  [{shortcut}]' if shortcut else '') + '\n' + action_label
        super().__init__(category.get('icon', 'tags'), label, parent)
        self.category_name = name
        self.category_color = category.get('color', 'auto')
        self.refresh_theme()
        self.origin = None

    def refresh_theme(self):
        color = QColor(COLORS[QApplication.instance().property('theme') or 'dark']['accent'] if self.category_color == 'auto' else self.category_color)
        if color.isValid():
            self.setStyleSheet(f'QPushButton {{ border-left: 5px solid {color.name()}; }}')

    def mousePressEvent(self, event):
        self.origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None and event.buttons() & Qt.LeftButton and (event.position().toPoint()-self.origin).manhattanLength() >= 12:
            mime = QMimeData()
            mime.setData(MIME, self.category_name.encode('utf-8'))
            drag = QDrag(self)
            drag.setMimeData(mime)
            self.setDown(False)
            self.origin = None
            drag.exec(Qt.MoveAction)
        else:
            super().mouseMoveEvent(event)

class CategoryStrip(QWidget):
    reordered = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        source = bytes(event.mimeData().data(MIME)).decode('utf-8')
        target = self.childAt(event.position().toPoint())
        while target is not None and not isinstance(target, CategoryButton):
            target = target.parentWidget()
            if target is self:
                target = None
        self.reordered.emit(source, target.category_name if target else '')
        event.acceptProposedAction()
