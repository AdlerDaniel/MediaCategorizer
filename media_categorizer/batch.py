"""Reviewable sequential batch processing with shared file reservations."""
import threading
from pathlib import Path
import uuid
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar, QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QTransform
from .constants import ACTION_LABELS, ACTIONS_WITH_RENAME, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from .naming import render_rename, filter_duplicate_tags
from .file_operations import perform_file_operation, rename_no_replace, path_key
from .photo_editor import load_photo, fingerprint, replace_photo
from .video_export import VideoExport, ExportCancelled, ExportOptions, signature, probe
from .ui import IconButton

OPERATION_LABELS = dict(rename='Переименовать', copy='Скопировать', move='Переместить',
                        skip='Без изменений', video='Преобразовать видео',
                        flip_h='Отразить горизонтально', flip_v='Отразить вертикально', rotate='Повернуть на 90°')


def plan_category(paths, category, categories, template):
    plans, targets = [], set()
    for index, source in enumerate(map(Path, paths)):
        action = category['action']
        tags, _ = filter_duplicate_tags(source, [category['name']], categories)
        name = render_rename(source, tags, index, template) if action in ACTIONS_WITH_RENAME and tags else source.name
        target = source.with_name(name) if action == 'rename' else Path(category['destination']).expanduser() / name
        kind = 'rename' if action == 'rename' else 'move' if 'move' in action else 'copy'
        error = None
        if path_key(source) == path_key(target):
            kind = 'skip'
        elif kind == 'rename' and (target.exists() or path_key(target) in targets):
            error = 'Имя уже занято'
        elif kind in ('copy', 'move'):
            initial = target
            number = 1
            while target.exists() or path_key(target) in targets:
                target = initial.with_name(f'{initial.stem} ({number}){initial.suffix}')
                number += 1
        targets.add(path_key(target))
        try:
            expected = signature(source)
        except OSError:
            expected, error = None, 'Источник недоступен'
        plans.append(dict(source=source, target=target, kind=kind, expected=expected, error=error))
    return plans

class BatchWorker(QThread):
    rowChanged = Signal(int, str)
    completed = Signal(object)
    progress = Signal(int)
    def __init__(self, plans, options, volume, parent=None):
        super().__init__(parent)
        self.plans, self.options, self.volume = plans, options, volume
        self.cancelled = threading.Event()
        self.export = None
    def cancel(self):
        self.cancelled.set()
        if self.export:
            self.export.cancel()
    def run(self):
        for index, plan in enumerate(self.plans):
            if self.cancelled.is_set():
                self.rowChanged.emit(index, 'Отменено')
                continue
            if plan.get('error'):
                self.rowChanged.emit(index, plan['error'])
                continue
            source, target, kind = plan['source'], plan['target'], plan['kind']
            self.rowChanged.emit(index, 'Выполняется…')
            result = dict(plan)
            try:
                if signature(source) != plan['expected']:
                    raise ValueError('Источник изменился после подготовки списка')
                if kind == 'skip':
                    self.rowChanged.emit(index, 'Без изменений')
                    continue
                if kind == 'rename':
                    rename_no_replace(source, target)
                elif kind in ('copy', 'move'):
                    perform_file_operation(kind, source, target)
                elif kind == 'video':
                    self.export = VideoExport()
                    if self.cancelled.is_set():
                        self.export.cancel()
                    self.export.run(source, 0, probe(source)['duration'], self.volume, plan['expected'], options=self.options)
                    self.export = None
                else:
                    expected = fingerprint(source)
                    image, fmt = load_photo(source)
                    if kind == 'flip_h':
                        image = image.flipped(Qt.Horizontal)
                    elif kind == 'flip_v':
                        image = image.flipped(Qt.Vertical)
                    elif kind == 'rotate':
                        image = image.transformed(QTransform().rotate(90))
                    replace_photo(source, image, fmt, expected)
                result['status'] = 'OK'
                self.rowChanged.emit(index, 'Готово')
            except ExportCancelled:
                result['status'] = 'CANCELLED'
                self.rowChanged.emit(index, 'Отменено; оригинал сохранён')
            except Exception as exc:
                result['status'], result['detail'] = 'ERROR', str(exc)
                self.rowChanged.emit(index, 'Ошибка: ' + str(exc))
            self.completed.emit(result)
            self.progress.emit(round((index+1)/len(self.plans)*100))

class BatchDialog(QDialog):
    def __init__(self, paths, main, parent=None):
        super().__init__(parent or main)
        self.main, self.paths = main, list(map(Path, paths))
        self.worker, self.plans = None, []
        self.results = []
        self.operation_id = 'batch-' + uuid.uuid4().hex
        self.setWindowTitle('Пакетная обработка')
        self.resize(1000, 640)
        self.mode = QComboBox()
        for label, value in [('Назначить категорию','category'), ('Преобразовать видео','video'), ('Отразить фото ↔','flip_h'), ('Отразить фото ↕','flip_v'), ('Повернуть фото +90°','rotate')]:
            self.mode.addItem(label, value)
        self.category = QComboBox()
        for category in main.categories:
            self.category.addItem(category['name'] + ' · ' + ACTION_LABELS[category['action']], category)
        self.resolution = QComboBox()
        for label,value in [('720p','720'),('1080p','1080'),('Исходное','source')]:
            self.resolution.addItem(label,value)
        self.fps = QComboBox()
        for value in (30,60,24,25,0):
            self.fps.addItem(str(value)+' fps' if value else 'Исходная частота',value)
        self.quality = QComboBox()
        for label,value in [('Сбалансированное','balanced'),('Высокое','high'),('Компактное','compact')]:
            self.quality.addItem(label,value)
        self.volume = QDoubleSpinBox()
        self.volume.setRange(0,200)
        self.volume.setValue(100)
        self.volume.setSuffix('% звука')
        self.table = QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['Источник','Результат','Действие','Статус'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.prepare_btn = IconButton('history', 'Подготовить список')
        self.start_btn = IconButton('check', 'Выполнить список')
        self.cancel_btn = IconButton('x', 'Закрыть')
        self.progress = QProgressBar()
        self.status = QLabel('Проверьте список перед запуском. Преобразования заменяют исходные файлы.')
        self.prepare_btn.clicked.connect(self.prepare)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        for widget in (self.mode,self.category,self.resolution,self.fps,self.quality,self.volume):
            row.addWidget(widget)
            widget.currentIndexChanged.connect(self.invalidate) if isinstance(widget,QComboBox) else widget.valueChanged.connect(self.invalidate)
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addWidget(self.table,1)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        for button in (self.prepare_btn,self.start_btn,self.cancel_btn):
            row.addWidget(button)
        layout.addLayout(row)
        self.invalidate()

    def invalidate(self):
        self.plans = []
        self.start_btn.setEnabled(False)
        video = self.mode.currentData() == 'video'
        self.category.setVisible(self.mode.currentData() == 'category')
        for widget in (self.resolution,self.fps,self.quality,self.volume):
            widget.setVisible(video)

    def prepare(self):
        try:
            mode = self.mode.currentData()
            if mode == 'category':
                self.plans = plan_category(self.paths,self.category.currentData(),self.main.categories,self.main.settings.get('rename_template'))
            else:
                self.plans = []
                for path in self.paths:
                    supported = path.suffix.lower() in (VIDEO_EXTENSIONS if mode == 'video' else IMAGE_EXTENSIONS)
                    self.plans.append(dict(source=path,target=path,kind=mode,expected=signature(path),error=None if supported else 'Пропуск: другой тип файла'))
            self.table.setRowCount(len(self.plans))
            for index, plan in enumerate(self.plans):
                for column, value in enumerate((plan['source'],plan['target'],OPERATION_LABELS[plan['kind']],plan['error'] or 'Готов к запуску')):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    self.table.setItem(index,column,item)
            self.start_btn.setEnabled(any(not p['error'] and p['kind'] != 'skip' for p in self.plans))
        except (OSError,ValueError,KeyError,TypeError) as exc:
            QMessageBox.warning(self,'Не удалось подготовить список',str(exc))

    def start(self):
        if self.worker or not self.plans:
            return
        paths = [path for plan in self.plans if not plan['error'] for path in (plan['source'],plan['target'])]
        try:
            self.main.file_reservations.acquire(self.operation_id,*paths)
        except RuntimeError as exc:
            QMessageBox.warning(self,'Файлы заняты',str(exc))
            return
        self.main.stop_all_video()
        self.results = []
        for widget in (self.mode,self.category,self.resolution,self.fps,self.quality,self.volume,self.prepare_btn,self.start_btn):
            widget.setEnabled(False)
        self.cancel_btn.setText('Остановить очередь')
        self.worker = BatchWorker(self.plans,ExportOptions(self.resolution.currentData(),self.fps.currentData(),self.quality.currentData()),self.volume.value()/100,self)
        self.worker.rowChanged.connect(lambda row,text: self.table.setItem(row,3,QTableWidgetItem(text)))
        self.worker.completed.connect(self.record)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self.finished_work)
        try:
            self.worker.start()
        except RuntimeError as exc:
            self.worker.deleteLater()
            self.worker = None
            self.main.file_reservations.release(self.operation_id)
            self.cancel_btn.setText('Закрыть')
            self.status.setText('Не удалось запустить очередь: ' + str(exc))

    def record(self, result):
        self.results.append(result)
        self.main.append_log('BATCH_'+result['kind'].upper(),result['source'],result['target'],result['status'],result.get('detail',''))

    def finished_work(self):
        self.worker.deleteLater()
        self.worker = None
        self.main.file_reservations.release(self.operation_id)
        self.main.refresh_after_batch(self.results)
        self.cancel_btn.setText('Закрыть')
        count = sum(result['status']=='OK' for result in self.results)
        self.status.setText(f'Завершено: {count} из {len(self.plans)}. Результат каждой операции указан в таблице.')

    def reject(self):
        if self.worker:
            self.worker.cancel()
            self.status.setText('Остановка: текущая файловая операция завершится, оставшиеся будут отменены.')
            return
        super().reject()
