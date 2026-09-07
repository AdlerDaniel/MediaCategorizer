"""Persistent size and icon-label preferences, independent from the color theme."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox
from .ui import IconButton, ToggleSwitch, UI_SIZES, apply_theme


class AppearanceDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle('Размер интерфейса')
        self.resize(460, 240)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Размер текста, кнопок и переключателей'))
        self.size = QComboBox()
        for key, (label, factor) in UI_SIZES.items():
            self.size.addItem(label, key)
        self.size.setCurrentIndex(max(0, self.size.findData(main.settings.get('ui_size', 'normal'))))
        layout.addWidget(self.size)
        self.labels = ToggleSwitch('Подписи основных значков')
        self.labels.setChecked(bool(main.settings.get('icon_labels', False)))
        layout.addWidget(self.labels)
        note = QLabel('Изменения применяются сразу во всех окнах и сохраняются для выбранной темы.')
        note.setWordWrap(True)
        layout.addWidget(note)
        close = IconButton('check', 'Готово')
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.size.currentIndexChanged.connect(self.apply)
        self.labels.toggled.connect(self.apply)

    def apply(self):
        self.main.settings.update(ui_size=self.size.currentData(), icon_labels=self.labels.isChecked())
        apply_theme(self.main.settings.get('theme', 'dark'), self.size.currentData(), self.labels.isChecked())
        self.main._persist_settings()
