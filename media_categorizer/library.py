"""Searchable library with reviewed batch processing."""
import os
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QThread, QTimer, QItemSelectionModel, QItemSelection
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog, QApplication)
from .constants import SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS
from .file_operations import path_key
from .video_export import probe
from .ui import IconButton, ToggleSwitch
from .batch import BatchDialog


def processed_paths(records):
    return {path_key(Path(value)) for record in records if record.get('status') == 'OK' and record.get('action') in {
                'RENAME', 'MOVE', 'COPY', 'RENAME+MOVE', 'RENAME+COPY',
                'BATCH_RENAME', 'BATCH_MOVE', 'BATCH_COPY', 'BATCH_VIDEO',
                'BATCH_FLIP_H', 'BATCH_FLIP_V', 'BATCH_ROTATE', 'EDIT', 'EDIT_PHOTO', 'EDIT_VIDEO'}
            for value in (record.get('source'), record.get('result')) if value}


def scan_files(folder, recursive=False, durations=False, cancelled=lambda: False, cache=None):
    records = []
    cache = cache if cache is not None else {}
    live = set()
    for base, directories, names in os.walk(folder, followlinks=False):
        directories[:] = sorted(d for d in directories if not Path(base, d).is_symlink()) if recursive else []
        for name in names:
            if cancelled():
                return records
            path = Path(base, name)
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            try:
                stat = path.stat()
                video = path.suffix.lower() in VIDEO_EXTENSIONS
                duration = None
                key = (str(path), stat.st_size, stat.st_mtime_ns)
                live.add(key)
                if video and durations:
                    try:
                        if key not in cache:
                            cache[key] = probe(path)['duration']
                        duration = cache[key]
                    except (OSError, ValueError):
                        cache[key] = None
                records.append(dict(path=path, size=stat.st_size, date=stat.st_mtime,
                                    video=video, duration=duration, identity=(stat.st_dev, stat.st_ino)))
            except OSError:
                continue
    for key in list(cache):
        if key not in live:
            del cache[key]
    return records


def filter_records(records, query='', kind='all', sort='name', processed=()):
    query = query.casefold().strip()
    values = [r for r in records if query in str(r['path']).casefold()
              and (kind == 'all' or kind == 'video' and r['video']
                   or kind == 'photo' and not r['video']
                   or kind == 'processed' and path_key(r['path']) in processed)]
    return sorted(values, key=lambda r: (
        r['path'].name.casefold() if sort == 'name' else
        r[sort] if r[sort] is not None else -1, str(r['path']).casefold()))


class ScanThread(QThread):
    def __init__(self, folder, recursive, durations, parent, cache=None):
        super().__init__(parent)
        self.folder, self.recursive, self.durations = folder, recursive, durations
        self.records, self.error = [], ''
        self.cache = cache

    def run(self):
        try:
            self.records = scan_files(self.folder, self.recursive, self.durations, self.isInterruptionRequested, self.cache)
        except Exception as exc:
            self.error = str(exc)


class LibraryDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.folder = Path(main.settings.get('last_folder') or Path.home())
        self.records, self.visible = [], []
        self.worker, self.close_pending = None, False
        self.duration_cache = {}
        self.background_scan = False
        self.processed = processed_paths(main.load_log_records(limit=1000000))
        self.setWindowTitle('Библиотека файлов')
        self.resize(1060, 700)
        self.folder_label = QLabel(str(self.folder))
        self.folder_label.setWordWrap(True)
        self.choose_btn = IconButton('folder-open', 'Выбрать папку')
        self.refresh_btn = IconButton('rotate-cw', 'Обновить')
        self.recursive = ToggleSwitch('Вложенные папки')
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск по имени или пути…')
        self.kind = QComboBox()
        for label, value in [('Все файлы','all'), ('Фото','photo'), ('Видео','video'), ('Обработанные','processed')]:
            self.kind.addItem(label, value)
        self.sort = QComboBox()
        for label, value in [('Имя А–Я','name'), ('Дата: сначала старые','date'), ('Размер: по возрастанию','size'), ('Длительность: по возрастанию','duration')]:
            self.sort.addItem(label, value)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Файл', 'Папка', 'Размер', 'Дата изменения', 'Длительность'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.status = QLabel()
        self.open_btn = IconButton('images', 'Открыть в просмотрщике')
        self.batch_btn = IconButton('tags', 'Обработать выбранные')
        self.all_btn = IconButton('check', 'Выбрать все')
        self.close_btn = IconButton('x', 'Закрыть')
        layout = QVBoxLayout(self)
        layout.addWidget(self.folder_label)
        row = QHBoxLayout()
        for w in (self.choose_btn, self.refresh_btn, self.recursive):
            row.addWidget(w)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        for w in (self.search, self.kind, self.sort):
            row.addWidget(w)
        layout.addLayout(row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        for w in (self.all_btn, self.open_btn, self.batch_btn, self.close_btn):
            row.addWidget(w)
        layout.addLayout(row)
        self.choose_btn.clicked.connect(self.choose_folder)
        self.refresh_btn.clicked.connect(self.scan)
        self.recursive.toggled.connect(self.scan)
        self.search.textChanged.connect(self.render)
        self.kind.currentIndexChanged.connect(self.render)
        self.sort.currentIndexChanged.connect(self.sort_changed)
        self.all_btn.clicked.connect(self.table.selectAll)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellDoubleClicked.connect(self.open_selection)
        self.open_btn.clicked.connect(self.open_selection)
        self.batch_btn.clicked.connect(self.batch)
        self.close_btn.clicked.connect(self.reject)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2000)
        self.refresh_timer.timeout.connect(self.auto_refresh)
        self.refresh_timer.start()
        QTimer.singleShot(0, self.scan)

    def auto_refresh(self):
        # Poll in a worker: handles network folders and newly created nested folders too.
        if self.isVisible() and not self.worker and QApplication.activeModalWidget() in (None, self):
            self.scan(background=True)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Папка библиотеки', str(self.folder))
        if folder:
            self.folder = Path(folder)
            self.scan()

    def sort_changed(self):
        if self.sort.currentData() == 'duration' and any(r['video'] and r['duration'] is None for r in self.records):
            self.scan()
        else:
            self.render()

    def scan(self, *args, background=False):
        if self.worker:
            return
        self.folder_label.setText(str(self.folder))
        self.background_scan = background
        for w in (self.choose_btn, self.refresh_btn, self.recursive, self.sort):
            w.setEnabled(False)
        if not background:
            self.open_btn.setEnabled(False)
            self.batch_btn.setEnabled(False)
            self.status.setText('Чтение файлов…' if self.sort.currentData() != 'duration' else 'Чтение длительности видео…')
        self.worker = ScanThread(self.folder, self.recursive.isChecked(), self.sort.currentData() == 'duration', self, self.duration_cache)
        self.worker.finished.connect(self.scanned)
        self.worker.start()

    def scanned(self):
        worker, self.worker = self.worker, None
        changed = self.records != worker.records
        self.records = worker.records
        error = worker.error
        worker.deleteLater()
        if self.close_pending:
            super().reject()
            return
        for w in (self.choose_btn, self.refresh_btn, self.recursive, self.sort):
            w.setEnabled(True)
        if changed or not self.background_scan:
            self.render()
        else:
            self.selection_changed()
        if error:
            self.status.setText('Ошибка чтения: ' + error)

    def render(self):
        chosen = {path_key(path) for path in self.selected()}
        identities = {r.get('identity') for r in self.visible if path_key(r['path']) in chosen and r.get('identity', (0, 0))[1]}
        scroll = self.table.verticalScrollBar().value()
        self.visible = filter_records(self.records, self.search.text(), self.kind.currentData(), self.sort.currentData(), self.processed)
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(self.visible))
        for row, record in enumerate(self.visible):
            duration = record['duration']
            values = (record['path'].name, str(record['path'].parent),
                      f"{record['size']/1024/1024:.2f} МБ",
                      datetime.fromtimestamp(record['date']).strftime('%d.%m.%Y %H:%M'),
                      f'{duration:.2f} с' if duration is not None else '—')
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(record['path']))
                self.table.setItem(row, col, item)
        selection = QItemSelection()
        for row, record in enumerate(self.visible):
            if path_key(record['path']) in chosen or record.get('identity') in identities:
                selection.select(self.table.model().index(row, 0), self.table.model().index(row, 4))
        self.table.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.blockSignals(False)
        self.selection_changed()

    def selected(self):
        return [self.visible[index.row()]['path'] for index in self.table.selectionModel().selectedRows() if index.row() < len(self.visible)]

    def selection_changed(self):
        count = len(self.selected())
        self.open_btn.setEnabled(count > 0 and self.worker is None)
        self.batch_btn.setEnabled(count > 0 and self.worker is None)
        if self.worker is None:
            self.status.setText(f'Файлов: {len(self.visible)} из {len(self.records)} · Выбрано: {count} · Ctrl/Shift — выбор нескольких')

    def open_selection(self, *args):
        selected = self.selected()
        if selected and self.worker is None:
            self.main.open_library_selection([r['path'] for r in self.visible], selected[0], self.folder)
            self.accept()

    def done(self, result):
        self.refresh_timer.stop()
        super().done(result)

    def batch(self):
        paths = self.selected()
        if paths and self.worker is None:
            BatchDialog(paths, self.main, self).exec()
            self.processed = processed_paths(self.main.load_log_records(limit=1000000))
            self.scan()

    def reject(self):
        self.refresh_timer.stop()
        if self.worker:
            self.close_pending = True
            self.worker.requestInterruption()
            self.status.setText('Завершение чтения…')
            return
        super().reject()
