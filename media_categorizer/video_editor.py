"""Separate video editor with trim controls, preview and cancellable export."""
from pathlib import Path
from fractions import Fraction
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QDoubleSpinBox, QProgressBar, QMessageBox, QComboBox, QTabWidget, QWidget, QAbstractSpinBox
from .ui import IconButton, ToggleSwitch
from .ui import DecimalSpinBox as QDoubleSpinBox
from .video_preview import VideoPreview
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
        self.resize(1000, 780)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.5)
        self.player.setAudioOutput(self.audio)
        self.video = VideoPreview(self.info)
        self.player.setVideoSink(self.video.videoSink())
        self.cuts = []
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
        self.tabs = QTabWidget()
        self.build_edit_tools()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Выберите отрезок и параметры экспорта. Сохранение заменит исходное видео.'))
        layout.addWidget(self.video, 1)
        transport = QHBoxLayout()
        self.previous_frame = IconButton('step-back', 'Предыдущий кадр', compact=True)
        self.next_frame = IconButton('step-forward', 'Следующий кадр', compact=True)
        self.previous_frame.clicked.connect(lambda: self.step_frame(-1))
        self.next_frame.clicked.connect(lambda: self.step_frame(1))
        for widget in (self.play_btn, self.previous_frame, self.next_frame, self.timeline, self.time_label):
            transport.addWidget(widget)
        layout.addLayout(transport)
        layout.addWidget(self.range_timeline)
        zoom_row = QHBoxLayout()
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(1, 64)
        self.zoom_slider.setValue(1)
        self.zoom_slider.setAccessibleName('Масштаб шкалы времени')
        self.zoom_slider.valueChanged.connect(self.range_timeline.set_zoom)
        self.pan_slider = QSlider(Qt.Horizontal)
        self.pan_slider.setRange(0, 1000)
        self.pan_slider.setAccessibleName('Прокрутка шкалы времени')
        self.pan_slider.valueChanged.connect(lambda value: self.range_timeline.set_offset(value/1000))
        self.range_timeline.viewChanged.connect(self.sync_timeline_view)
        for widget in (QLabel('Масштаб'), self.zoom_slider, QLabel('Прокрутка'), self.pan_slider):
            zoom_row.addWidget(widget)
        layout.addLayout(zoom_row)
        trim = QHBoxLayout()
        for widget in (QLabel('Начало'), self.start, self.mark_start, QLabel('Конец'), self.end, self.mark_end):
            trim.addWidget(widget)
        self.trim_layout.insertLayout(0, trim)
        sound = QHBoxLayout()
        for widget in (QLabel('Громкость'), self.volume, self.volume_label, self.reset_btn):
            sound.addWidget(widget)
        self.sound_layout.addLayout(sound)
        layout.addWidget(self.tabs)
        options_row = QHBoxLayout()
        for widget in (self.resolution, self.fps, self.quality, self.estimate):
            options_row.addWidget(widget)
        layout.addLayout(options_row)
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
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))
        self.assets = TimelineAssets(self.path, self.info["duration"], bool(self.info["audio"]), self)
        self.assets.finished.connect(self.assets_ready)
        self.assets.start()

    def options(self):
        return ExportOptions(self.resolution.currentData(), self.fps.currentData(), self.quality.currentData(),
                             tuple(self.cuts), self.rotation.currentData(), self.mirror.isChecked(),
                             self.crop_ratio.currentData(), self.normalize.isChecked())

    def build_edit_tools(self):
        trim_tab, crop_tab, sound_tab = QWidget(), QWidget(), QWidget()
        self.trim_layout = QVBoxLayout(trim_tab)
        self.sound_layout = QVBoxLayout(sound_tab)
        crop_layout = QVBoxLayout(crop_tab)
        for widget, label in ((trim_tab, 'Монтаж'), (crop_tab, 'Кадрирование'), (sound_tab, 'Звук')):
            self.tabs.addTab(widget, label)
        cut_row = QHBoxLayout()
        self.cut_start, self.cut_end = QDoubleSpinBox(), QDoubleSpinBox()
        for spin, label in ((self.cut_start, 'Начало удаляемого фрагмента'), (self.cut_end, 'Конец удаляемого фрагмента')):
            spin.setRange(0, self.info['duration'])
            spin.setDecimals(6)
            spin.setSingleStep(1/self.frame_rate)
            spin.setButtonSymbols(QAbstractSpinBox.PlusMinus)
            spin.setSuffix(' с')
            spin.setAccessibleName(label)
        self.cut_end.setValue(min(1, self.info['duration']))
        cut_in = IconButton('step-forward', 'Отсюда', compact=False)
        cut_out = IconButton('step-back', 'Досюда', compact=False)
        cut_in.clicked.connect(lambda: self.cut_start.setValue(self.player.position()/1000))
        cut_out.clicked.connect(lambda: self.cut_end.setValue(self.player.position()/1000))
        self.add_cut_btn = IconButton('x', 'Вырезать')
        self.add_cut_btn.clicked.connect(self.add_cut)
        self.cut_list = QComboBox()
        self.cut_list.setMinimumContentsLength(20)
        restore = IconButton('undo-2', 'Вернуть')
        restore.clicked.connect(self.restore_cut)
        for widget in (QLabel('Удалить'), self.cut_start, cut_in, self.cut_end, cut_out, self.add_cut_btn):
            cut_row.addWidget(widget)
        self.trim_layout.addLayout(cut_row)
        cuts_row = QHBoxLayout()
        cuts_row.addWidget(self.cut_list, 1)
        cuts_row.addWidget(restore)
        self.trim_layout.addLayout(cuts_row)
        self.rotation, self.crop_ratio = QComboBox(), QComboBox()
        for angle in (0, 90, 180, 270):
            self.rotation.addItem(f'Поворот {angle}°', angle)
        for label, value in (('Без обрезки', ''), ('16:9', '16:9'), ('9:16', '9:16'), ('Квадрат 1:1', '1:1')):
            self.crop_ratio.addItem(label, value)
        self.mirror = ToggleSwitch('Отражение по горизонтали')
        framing = QHBoxLayout()
        for widget in (self.rotation, self.crop_ratio, self.mirror):
            framing.addWidget(widget)
        crop_layout.addLayout(framing)
        crop_layout.addWidget(QLabel('Обрезка по центру. Предпросмотр сверху показывает итоговое кадрирование.'))
        crop_layout.addStretch()
        self.normalize = ToggleSwitch('Выровнять громкость и ограничить пики')
        self.sound_layout.addWidget(self.normalize)
        note = QLabel('Нормализация применяется при сохранении: цель −16 LUFS, ограничение пиков.\nПредпросмотр воспроизводит исходный звук с выбранной ручной громкостью.')
        note.setWordWrap(True)
        self.sound_layout.addWidget(note)
        self.rotation.currentIndexChanged.connect(self.update_controls)
        self.crop_ratio.currentIndexChanged.connect(self.update_controls)
        self.mirror.toggled.connect(self.update_controls)
        self.normalize.toggled.connect(self.update_controls)

    def sync_timeline_view(self):
        timeline = self.range_timeline
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(round(timeline.zoom))
        self.zoom_slider.blockSignals(False)
        available = timeline.duration-timeline.duration/timeline.zoom
        self.pan_slider.blockSignals(True)
        self.pan_slider.setValue(round(timeline.offset/available*1000) if available else 0)
        self.pan_slider.blockSignals(False)
        self.pan_slider.setEnabled(available > 0 and self.worker is None)

    def step_frame(self, direction):
        self.player.pause()
        frame = round(self.player.position()/1000*self.frame_rate)+direction
        self.player.setPosition(round(max(0, min(self.info['duration'], frame/self.frame_rate))*1000))

    def add_cut(self):
        left, right = self.cut_start.value(), self.cut_end.value()
        if right-left < 1/self.frame_rate:
            self.status.setText('Удаляемый фрагмент должен быть не короче одного кадра.')
            return
        self.cuts.append((left, right))
        self.refresh_cuts()

    def restore_cut(self):
        index = self.cut_list.currentIndex()
        if 0 <= index < len(self.cuts):
            self.cuts.pop(index)
            self.refresh_cuts()

    def refresh_cuts(self):
        self.cut_list.clear()
        for a, b in self.cuts:
            self.cut_list.addItem(f'{a:.3f} — {b:.3f} с')
        self.range_timeline.cuts = tuple(self.cuts)
        self.update_controls()

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
            self.range_timeline.waveform = self.assets.waveform
            self.range_timeline.update()

    def stop_assets(self):
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
        for widget in (self.tabs, self.start, self.end, self.mark_start, self.mark_end, self.timeline, self.play_btn, self.reset_btn, self.range_timeline, self.resolution, self.fps, self.quality, self.previous_frame, self.next_frame, self.zoom_slider):
            widget.setEnabled(not busy)
        self.sync_timeline_view()
        self.normalize.setEnabled(not busy and bool(self.info['audio']))
        self.volume.setEnabled(not busy and bool(self.info['audio']))
        self.save_btn.setEnabled(not busy and duration >= 1/30)
        self.range_timeline.set_range(self.start.value(), self.end.value())
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
        self.player.setSource(QUrl())  # Release Windows file handles before replacement.
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
        self.stop_assets()
        if self.worker:
            self.close_after_cancel = True
            self.worker.job.cancel()
            self.status.setText('Отмена сохранения…')
            return
        self.player.stop()
        self.player.setSource(QUrl())
        super().reject()
