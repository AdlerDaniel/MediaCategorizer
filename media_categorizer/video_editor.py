"""Separate video editor with trim controls, preview and cancellable export."""
from pathlib import Path
from fractions import Fraction
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal, QSettings
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QMediaDevices, QVideoFrame
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QDoubleSpinBox, QProgressBar, QMessageBox, QComboBox, QTabWidget, QWidget, QAbstractSpinBox
from .ui import IconButton, ToggleSwitch
from .ui import TimeSpinBox as QDoubleSpinBox
from .video_preview import VideoPreview
from .editor_widgets import ToolPanel, RatioCards, PreviewHost, ElidedLabel, field
from .video_export import VideoExport, ExportCancelled, ExportOptions, probe, signature
from .timeline import RangeTimeline, TimelineAssets


class ExportThread(QThread):
    progress = Signal(int)

    def __init__(self, path, start, end, volume, expected, parent=None, options=None):
        super().__init__(parent)
        self.job = VideoExport()
        self.arguments = path, start, end, volume, expected
        self.options = options
        self.error = None
        self.was_cancelled = False

    def run(self):
        try:
            self.job.run(*self.arguments, self.progress.emit, options=self.options)
        except ExportCancelled:
            self.was_cancelled = True
        except Exception as exc:
            self.error = str(exc)


class VideoEditor(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.expected = signature(path)
        self.info = probe(path)
        self.worker = None
        self.assets = None
        self.close_after_cancel = False
        self.setWindowTitle('Редактировать видео — ' + self.path.name)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        self.resize(1100, 800)
        from .settings import app_config_dir
        self.window_settings = QSettings(str(app_config_dir()/'editor-windows.ini'),QSettings.IniFormat)
        saved = self.window_settings.value('video/geometry')
        if saved: self.restoreGeometry(saved)
        self.finished.connect(self.remember_window)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.5)
        self.player.setAudioOutput(self.audio)
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(self.refresh_audio_device)
        self.video = VideoPreview(self.info)
        self.player.setVideoSink(self.video.videoSink())
        self.cuts = []
        self.custom_crop = ()
        self.splits = []
        self.edit_history = []
        self.warming_preview = True
        try:
            self.frame_rate = float(Fraction(self.info['stream'].get('avg_frame_rate', '30/1'))) or 30
        except (ValueError, ZeroDivisionError):
            self.frame_rate = 30
        self.play_btn = IconButton('play', 'Воспроизвести выделенный отрезок', compact=True)
        self.play_btn.clicked.connect(self.toggle_play)
        self.player.playbackStateChanged.connect(lambda state: self.play_btn.set_playing(state == QMediaPlayer.PlayingState))
        self.range_timeline = RangeTimeline(self.info["duration"])
        self.range_timeline.frame_rate = self.frame_rate
        self.range_timeline.seekRequested.connect(self.player.setPosition)
        self.timeline = QSlider(Qt.Horizontal)
        self.timeline.setRange(0, round(self.info['duration']*1000))
        self.timeline.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.position_changed)
        self.time_label = QLabel('00:00.000')
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setDecimals(6)
            spin.setSingleStep(1/self.frame_rate)
            spin.setButtonSymbols(QAbstractSpinBox.PlusMinus)
            spin.setRange(0, self.info['duration'])
            spin.setSuffix(' с')
            spin.setMinimumWidth(130)
        self.end.setValue(self.info['duration'])
        self.start.setAccessibleName('Начало отрезка в секундах')
        self.end.setAccessibleName('Конец отрезка в секундах')
        self.mark_start = IconButton('step-forward', 'Начало здесь')
        self.mark_end = IconButton('step-back', 'Конец здесь')
        self.mark_start.clicked.connect(lambda: self.start.setValue(self.player.position()/1000))
        self.mark_end.clicked.connect(lambda: self.end.setValue(self.player.position()/1000))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 200)
        self.volume.setValue(100)
        self.volume.setAccessibleName('Громкость от 0 до 200 процентов')
        self.volume_label = QLabel('100%')
        self.volume.valueChanged.connect(self.volume_changed)
        self.reset_btn = IconButton('undo-2', 'Сбросить')
        self.reset_btn.clicked.connect(self.reset)
        self.resolution = QComboBox()
        for label, value in [('720p', '720'), ('1080p', '1080'), ('Исходное разрешение', 'source')]:
            self.resolution.addItem(label, value)
        self.fps = QComboBox()
        for label, value in [('30 fps', 30), ('24 fps', 24), ('25 fps', 25), ('60 fps', 60), ('Исходная частота', 0)]:
            self.fps.addItem(label, value)
        self.quality = QComboBox()
        for label, value in [('Сбалансированное', 'balanced'), ('Высокое качество', 'high'), ('Меньше размер', 'compact')]:
            self.quality.addItem(label, value)
        self.estimate = QLabel()
        for widget in (self.resolution, self.fps, self.quality):
            widget.currentIndexChanged.connect(self.update_controls)
        self.save_btn = IconButton('check', 'Сохранить видео')
        self.save_btn.setProperty('primary', True)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn = IconButton('x', 'Закрыть')
        self.cancel_btn.clicked.connect(self.cancel_or_close)
        self.status = QLabel()
        self.progress = QProgressBar()
        self.progress.hide()
        self.start.valueChanged.connect(self.update_controls)
        self.end.valueChanged.connect(self.update_controls)
        self.tabs = ToolPanel()
        self.build_edit_tools()
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = ElidedLabel('Видео · '+self.path.name)
        title.setObjectName('sectionTitle')
        header.addWidget(title,1)
        header.addWidget(self.reset_btn)
        self.reset_btn.setProperty('destructive',True)
        layout.addLayout(header)
        self.view_zoom_label = QLabel('100%')
        zoom_out, zoom_in = IconButton('minus','Уменьшить',compact=True), IconButton('plus','Увеличить',compact=True)
        fit = IconButton('maximize','Вписать')
        zoom_out.clicked.connect(lambda: self.set_view_zoom(self.video.view_zoom/1.25))
        zoom_in.clicked.connect(lambda: self.set_view_zoom(self.video.view_zoom*1.25))
        fit.clicked.connect(lambda: self.set_view_zoom(1))
        self.preview_host = PreviewHost(self.video,(zoom_out,self.view_zoom_label,zoom_in,fit), floating=False)
        workspace = QHBoxLayout()
        workspace.setContentsMargins(0,0,0,0)
        workspace.addWidget(self.preview_host,1)
        workspace.addWidget(self.tabs)
        container = QWidget()
        container.setLayout(workspace)
        layout.addWidget(container,1)
        transport = QHBoxLayout()
        self.previous_frame = IconButton('step-back', 'Предыдущий кадр', compact=True)
        self.next_frame = IconButton('step-forward', 'Следующий кадр', compact=True)
        self.previous_frame.clicked.connect(lambda: self.step_frame(-1))
        self.next_frame.clicked.connect(lambda: self.step_frame(1))
        for widget in (self.play_btn, self.previous_frame, self.next_frame, self.timeline, self.time_label):
            transport.addWidget(widget)
        layout.addLayout(transport)
        self.edit_toolbar = QWidget()
        actions = QHBoxLayout(self.edit_toolbar)
        actions.setContentsMargins(0,0,0,0)
        self.undo_edit = IconButton('undo-2','Отменить монтаж',compact=True)
        self.trim_left = IconButton('step-forward','Обрезать всё слева от курсора',compact=True)
        self.trim_right = IconButton('step-back','Обрезать всё справа от курсора',compact=True)
        self.split_btn = IconButton('scissors','Разделить фрагмент',compact=True)
        self.delete_fragment = IconButton('trash-2','Удалить выбранный фрагмент',compact=True)
        self.mirror_btn = IconButton('flip-horizontal-2','Отразить видео',compact=True)
        self.crop_dialog_btn = IconButton('crop','Кадрировать',compact=True)
        self.undo_edit.clicked.connect(self.undo_montage)
        self.trim_left.clicked.connect(lambda:self.trim_at_cursor(True))
        self.trim_right.clicked.connect(lambda:self.trim_at_cursor(False))
        self.split_btn.clicked.connect(self.split_fragment)
        self.delete_fragment.clicked.connect(self.remove_fragment)
        self.mirror_btn.setCheckable(True)
        self.mirror_btn.clicked.connect(self.mirror.toggle)
        self.range_timeline.segmentSelected.connect(lambda index:self.delete_fragment.setEnabled(index>=0 and self.worker is None))
        self.crop_dialog_btn.clicked.connect(self.open_crop)
        for button in (self.undo_edit,self.trim_left,self.trim_right,self.split_btn,self.delete_fragment,self.mirror_btn,self.crop_dialog_btn):actions.addWidget(button)
        actions.addStretch()
        layout.addWidget(self.edit_toolbar)
        layout.addWidget(self.range_timeline)
        self.asset_timer = QTimer(self)
        self.asset_timer.setSingleShot(True)
        self.asset_timer.setInterval(200)
        self.asset_timer.timeout.connect(self.refresh_assets)
        self.range_timeline.viewChanged.connect(lambda: self.asset_timer.start())
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.save_btn)
        layout.addLayout(footer)
        self.player.errorOccurred.connect(self.preview_error)
        self.range_timeline.rangeChanged.connect(self.range_changed)
        self.update_controls()
        self.player.mediaStatusChanged.connect(self.warm_status)
        self.video.videoSink().videoFrameChanged.connect(self.warm_frame)
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))
        self.assets = TimelineAssets(self.path, self.info["duration"], bool(self.info["audio"]), self)
        self.assets.finished.connect(self.assets_ready)
        self.assets.start()
        self.initial_edit_state = self.edit_state()

    def edit_state(self):
        return (self.start.value(), self.end.value(), self.volume.value(), self.options(), tuple(self.splits))

    def options(self):
        return ExportOptions(self.resolution.currentData(), self.fps.currentData(), self.quality.currentData(),
                             tuple(self.cuts), self.rotation.currentData(), self.mirror.isChecked(),
                             self.crop_ratio.currentData(), self.normalize.isChecked(), self.custom_crop)

    def refresh_audio_device(self):
        device = QMediaDevices.defaultAudioOutput()
        if self.audio.device() != device:
            self.audio.setDevice(device)
        self.audio.setMuted(False)
        self.audio.setVolume(self.volume.value()/200)

    def set_view_zoom(self,value):
        self.video.set_zoom(value)
        self.view_zoom_label.setText(f'{self.video.view_zoom*100:.0f}%')

    def build_edit_tools(self):
        trim_tab,crop_tab,sound_tab,export_tab = QWidget(),QWidget(),QWidget(),QWidget()
        self.trim_layout = QVBoxLayout(trim_tab)
        self.sound_layout = QVBoxLayout(sound_tab)
        crop_layout,export_layout = QVBoxLayout(crop_tab),QVBoxLayout(export_tab)
        for widget,label in ((trim_tab,'Монтаж'),(crop_tab,'Кадрирование'),(sound_tab,'Звук'),(export_tab,'Экспорт')):
            self.tabs.addTab(widget,label)
        def timing(label,spin,button):
            row = QHBoxLayout()
            row.setContentsMargins(0,0,0,0)
            button.compact = True
            button.refresh_size()
            row.addWidget(spin,1)
            row.addWidget(button)
            container = QWidget()
            container.setLayout(row)
            field(self.trim_layout,label,container)
        timing('Начало диапазона',self.start,self.mark_start)
        timing('Конец диапазона',self.end,self.mark_end)
        hint = QLabel('Выберите фрагмент на таймлайне. Инструменты монтажа расположены над ним.')
        hint.setWordWrap(True)
        self.trim_layout.addWidget(hint)
        self.rotation,self.crop_ratio = QComboBox(),QComboBox()
        for angle in (0,90,180,270):
            self.rotation.addItem(f'{angle}°',angle)
        for label,value in (('Исходный',''),('16:9','16:9'),('9:16','9:16'),('1:1','1:1')):
            self.crop_ratio.addItem(label,value)
        self.crop_ratio.setParent(self)
        self.crop_ratio.hide()
        self.ratio_cards = RatioCards(self.crop_ratio)
        for button in self.ratio_cards.buttons:
            button.clicked.connect(self.clear_custom_crop)
        crop_layout.addWidget(self.ratio_cards)
        field(crop_layout,'Поворот',self.rotation)
        self.mirror = ToggleSwitch('Отразить ↔')
        crop_layout.addWidget(self.mirror)
        crop_button = IconButton('crop','Настроить рамку…')
        crop_button.clicked.connect(self.open_crop)
        crop_layout.addWidget(crop_button)
        note = QLabel('Выберите формат или настройте рамку перетаскиванием границ.')
        note.setWordWrap(True)
        crop_layout.addWidget(note)
        self.normalize = ToggleSwitch('Нормализация звука')
        self.sound_layout.addWidget(self.normalize)
        field(self.sound_layout,'Громкость',self.volume)
        self.sound_layout.addWidget(self.volume_label)
        note = QLabel('Нормализация и ограничение пиков применяются при сохранении. Предпросмотр: исходный звук с ручной громкостью.')
        note.setWordWrap(True)
        self.sound_layout.addWidget(note)
        for label,widget in (('Разрешение',self.resolution),('Частота кадров',self.fps),('Качество',self.quality)):
            field(export_layout,label,widget)
        export_layout.addWidget(self.estimate)
        note = QLabel('Сохранение заменит исходное видео после проверки результата.')
        note.setWordWrap(True)
        export_layout.addWidget(note)
        self.rotation.currentIndexChanged.connect(self.clear_custom_crop)
        self.crop_ratio.currentIndexChanged.connect(self.clear_custom_crop)
        self.mirror.toggled.connect(self.update_controls)
        self.normalize.toggled.connect(self.update_controls)

    def step_frame(self, direction):
        self.player.pause()
        frame = round(self.player.position()/1000*self.frame_rate)+direction
        self.player.setPosition(round(max(0, min(self.info['duration'], frame/self.frame_rate))*1000))

    def remember_window(self, *args):
        self.window_settings.setValue('video/geometry',self.saveGeometry())
        self.window_settings.sync()

    def clear_custom_crop(self):
        self.custom_crop=()
        self.update_controls()

    def open_crop(self):
        from .video_crop import VideoCropDialog
        from PySide6.QtGui import QTransform
        self.player.pause()
        # Qt toImage already applies frame/surface presentation rotation.
        image=self.video.videoSink().videoFrame().toImage()
        if image.isNull():
            self.status.setText('Кадр ещё загружается. Попробуйте через секунду.')
            return
        image=image.scaled(round(self.info["width"]),round(self.info["height"]),Qt.IgnoreAspectRatio,Qt.SmoothTransformation)
        image=image.transformed(QTransform().rotate(self.rotation.currentData()))
        if self.mirror.isChecked():image=image.flipped(Qt.Horizontal)
        dialog=VideoCropDialog(image,self.custom_crop,self)
        if dialog.exec()==QDialog.Accepted:
            self.custom_crop=dialog.crop_rect()
            self.update_controls()

    def remember_montage(self):
        self.edit_history.append((self.start.value(),self.end.value(),list(self.cuts),list(self.splits)))

    def undo_montage(self):
        if not self.edit_history:return
        a,b,self.cuts,self.splits=self.edit_history.pop()
        self.range_changed(a,b)
        self.refresh_cuts()

    def trim_at_cursor(self, left):
        t=self.range_timeline.seconds(self.range_timeline.x(self.player.position()/1000))
        if not self.start.value()<t<self.end.value():return
        self.remember_montage()
        (self.start if left else self.end).setValue(t)
        self.refresh_cuts()

    def split_fragment(self):
        t=self.range_timeline.seconds(self.range_timeline.x(self.player.position()/1000))
        if any(a+1/self.frame_rate<=t<=b-1/self.frame_rate for a,b in self.range_timeline.fragments()):
            self.remember_montage()
            self.splits.append(t)
            self.refresh_cuts()

    def remove_fragment(self):
        fragments=self.range_timeline.fragments()
        index=self.range_timeline.selected
        if 0<=index<len(fragments):
            self.remember_montage()
            self.cuts.append(fragments[index])
            self.refresh_cuts()

    def refresh_cuts(self):
        self.range_timeline.cuts=tuple(self.cuts)
        self.range_timeline.splits=list(self.splits)
        self.range_timeline.selected=-1
        self.update_controls()

    def warm_frame(self, frame):
        if self.warming_preview and frame.isValid():
            self.warming_preview=False
            QTimer.singleShot(0,self,self.finish_warm_preview)

    def finish_warm_preview(self):
        if self.player.source().isEmpty():return
        self.player.pause()
        self.player.setPosition(0)
        self.audio.setMuted(False)

    def warm_status(self, status):
        if self.warming_preview and status==QMediaPlayer.LoadedMedia:
            self.audio.setMuted(True)
            self.player.play()

    def refresh_assets(self):
        if self.worker:return
        if self.assets and self.assets.isRunning():
            self.assets.stop()
            self.asset_timer.start()
            return
        if self.assets:self.assets.deleteLater()
        t=self.range_timeline
        self.assets=TimelineAssets(self.path,self.info['duration'],bool(self.info['audio']) and self.range_timeline.waveform.isNull(),self,offset=t.offset,span=t.duration/t.zoom,count=max(8,min(64,round(t.width()/max(24,((t.height()-56)*.56)*(self.info['width']/self.info['height'])))+2)))
        self.assets.finished.connect(self.assets_ready)
        self.assets.start()

    def range_changed(self, start, end):
        self.start.blockSignals(True)
        self.end.blockSignals(True)
        self.start.setValue(start)
        self.end.setValue(end)
        self.start.blockSignals(False)
        self.end.blockSignals(False)
        self.update_controls()

    def assets_ready(self):
        if self.assets:
            self.range_timeline.images = self.assets.images
            self.range_timeline.image_offset = self.assets.offset
            self.range_timeline.image_span = self.assets.span
            if not self.assets.waveform.isNull():
                self.range_timeline.waveform = self.assets.waveform
            self.range_timeline.update()

    def stop_assets(self):
        self.asset_timer.stop()
        if self.assets and self.assets.isRunning():
            self.assets.stop()
            self.assets.wait()

    def accept(self):
        self.stop_assets()
        super().accept()

    def preview_error(self, error, message):
        if not self.worker:
            self.status.setText('Предпросмотр недоступен: ' + message + '. Можно сохранить выбранный отрезок.')

    def update_controls(self):
        busy = self.worker is not None
        options = self.options()
        duration = sum(b-a for a, b in options.segments(self.start.value(), self.end.value()))
        for widget in (self.tabs, self.start, self.end, self.mark_start, self.mark_end, self.timeline, self.play_btn, self.reset_btn, self.range_timeline, self.resolution, self.fps, self.quality, self.previous_frame, self.next_frame, self.edit_toolbar):
            widget.setEnabled(not busy)

        self.delete_fragment.setEnabled(not busy and 0<=self.range_timeline.selected<len(self.range_timeline.fragments()))
        self.undo_edit.setEnabled(not busy and bool(self.edit_history))
        self.mirror_btn.setChecked(self.mirror.isChecked())
        self.normalize.setEnabled(not busy and bool(self.info['audio']))
        self.volume.setEnabled(not busy and bool(self.info['audio']))
        self.save_btn.setEnabled(not busy and duration >= 1/30)
        self.range_timeline.set_range(self.start.value(), self.end.value())
        source_ratio=self.info["width"]/self.info["height"]
        self.ratio_cards.buttons[0].ratio=1/source_ratio if options.rotation%180 else source_ratio
        self.ratio_cards.buttons[0].update()
        self.video.set_options(options)
        size = self.options().estimate_bytes(self.info, max(0,duration))/1024/1024
        self.estimate.setText(f"≈ {size*.7:.1f}–{size*1.4:.1f} МБ")
        self.estimate.setToolTip("Ориентировочный размер: зависит от содержимого и контейнера")
        self.cancel_btn.setText('Отменить сохранение' if busy else 'Закрыть')
        if not busy:
            sound = ('Нормализация при сохранении.' if options.normalize else '0% — без звука, 100% — исходная громкость.') if self.info['audio'] else 'В видео нет звуковой дорожки.'
            self.status.setText(f'Останется {max(0, duration):.3f} с. ' + sound)

    def volume_changed(self, value):
        self.volume_label.setText(f'{value}%')
        # Preview has 6 dB of headroom so 200% is audible, not clamped at 100%.
        self.audio.setVolume(value/200)

    def position_changed(self, value):
        self.range_timeline.set_position(value)
        if not self.timeline.isSliderDown():
            self.timeline.setValue(value)
        seconds = value/1000
        self.time_label.setText(f'{int(seconds//60):02}:{seconds%60:06.3f}')
        self.time_label.setToolTip(f'Кадр ≈ {round(seconds*self.frame_rate)} · {self.frame_rate:.3f} fps исходника')
        if seconds >= self.end.value() and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            for a, b in self.cuts:
                if a <= seconds < b:
                    self.player.setPosition(round(min(b, self.end.value())*1000))
                    return

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            position = self.player.position()/1000
            if position < self.start.value() or position >= self.end.value():
                self.player.setPosition(round(self.start.value()*1000))
            self.player.play()

    def reset(self):
        self.start.setValue(0)
        self.end.setValue(self.info['duration'])
        self.volume.setValue(100)
        self.cuts.clear()
        self.splits.clear()
        self.custom_crop=()
        self.rotation.setCurrentIndex(0)
        self.crop_ratio.setCurrentIndex(0)
        self.mirror.setChecked(False)
        self.normalize.setChecked(False)
        self.refresh_cuts()

    def save(self):
        if self.worker or sum(b-a for a, b in self.options().segments(self.start.value(), self.end.value())) < 1/30:
            return
        self.stop_assets()
        self.player.stop()
        self.warming_preview=False
        self.player.setSource(QUrl())  # Release Windows file handles before replacement.
        self.video.videoSink().setVideoFrame(QVideoFrame())
        self.worker = ExportThread(self.path, self.start.value(), self.end.value(), self.volume.value()/100, self.expected, self, options=self.options())
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self.export_finished)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText('Обработка видео… Исходник будет заменён после проверки результата.')
        self.update_controls()
        QTimer.singleShot(100, self.worker, self.worker.start)

    def export_finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        self.progress.hide()
        if not worker.error and not worker.was_cancelled:
            self.accept()
            return
        if self.close_after_cancel:
            super().reject()
            return
        self.update_controls()
        self.warming_preview=True
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))
        if worker.error:
            QMessageBox.warning(self, 'Не удалось сохранить видео', worker.error)
        else:
            self.status.setText('Сохранение отменено. Исходное видео не изменено.')

    def cancel_or_close(self):
        if self.worker:
            self.worker.job.cancel()
            self.status.setText('Отмена сохранения…')
        else:
            self.reject()

    def reject(self):
        if self.worker:
            answer = QMessageBox.question(self, 'Сохранение выполняется',
                'Остановить сохранение и закрыть редактор без сохранения правок?',
                QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                return
            self.close_after_cancel = True
            self.worker.job.cancel()
            self.status.setText('Отмена сохранения…')
            return
        if self.edit_state() != self.initial_edit_state:
            answer = QMessageBox.question(self, 'Несохранённые изменения',
                'Сохранить изменения видео перед закрытием?',
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer == QMessageBox.Save:
                self.save()
                return
            if answer != QMessageBox.Discard:
                return
        self.stop_assets()
        self.warming_preview=False
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.videoSink().setVideoFrame(QVideoFrame())
        super().reject()
