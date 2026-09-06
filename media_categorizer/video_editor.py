"""Separate video editor with trim controls, preview and cancellable export."""
from pathlib import Path
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QDoubleSpinBox, QProgressBar, QMessageBox
from .ui import IconButton
from .video_export import VideoExport, ExportCancelled, probe, signature


class ExportThread(QThread):
    progress = Signal(int)

    def __init__(self, path, start, end, volume, expected, parent=None):
        super().__init__(parent)
        self.job = VideoExport()
        self.arguments = path, start, end, volume, expected
        self.error = None
        self.was_cancelled = False

    def run(self):
        try:
            self.job.run(*self.arguments, self.progress.emit)
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
        self.close_after_cancel = False
        self.setWindowTitle('Редактировать видео — ' + self.path.name)
        self.resize(1000, 780)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(.5)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 240)
        self.player.setVideoOutput(self.video)
        self.play_btn = IconButton('play', 'Воспроизвести выделенный отрезок', compact=True)
        self.play_btn.clicked.connect(self.toggle_play)
        self.player.playbackStateChanged.connect(lambda state: self.play_btn.set_playing(state == QMediaPlayer.PlayingState))
        self.timeline = QSlider(Qt.Horizontal)
        self.timeline.setRange(0, round(self.info['duration']*1000))
        self.timeline.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.position_changed)
        self.time_label = QLabel('00:00.000')
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setDecimals(3)
            spin.setSingleStep(.1)
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
        self.save_btn = IconButton('check', 'Сохранить · 720p / 30 fps')
        self.save_btn.setProperty('primary', True)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn = IconButton('x', 'Закрыть')
        self.cancel_btn.clicked.connect(self.cancel_or_close)
        self.status = QLabel()
        self.progress = QProgressBar()
        self.progress.hide()
        self.start.valueChanged.connect(self.update_controls)
        self.end.valueChanged.connect(self.update_controls)
        layout = QVBoxLayout(self)
        portrait = self.info['height'] > self.info['width']
        layout.addWidget(QLabel(('720 × 1280' if portrait else '1280 × 720') + ' · 30 кадров/с · сохранение заменит исходное видео'))
        layout.addWidget(self.video, 1)
        transport = QHBoxLayout()
        for widget in (self.play_btn, self.timeline, self.time_label):
            transport.addWidget(widget)
        layout.addLayout(transport)
        trim = QHBoxLayout()
        for widget in (QLabel('Начало'), self.start, self.mark_start, QLabel('Конец'), self.end, self.mark_end):
            trim.addWidget(widget)
        layout.addLayout(trim)
        sound = QHBoxLayout()
        for widget in (QLabel('Громкость'), self.volume, self.volume_label, self.reset_btn):
            sound.addWidget(widget)
        layout.addLayout(sound)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.save_btn)
        layout.addLayout(footer)
        self.player.errorOccurred.connect(self.preview_error)
        self.update_controls()
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))

    def preview_error(self, error, message):
        if not self.worker:
            self.status.setText('Предпросмотр недоступен: ' + message + '. Можно сохранить выбранный отрезок.')

    def update_controls(self):
        busy = self.worker is not None
        duration = self.end.value()-self.start.value()
        for widget in (self.start, self.end, self.mark_start, self.mark_end, self.timeline, self.play_btn, self.reset_btn):
            widget.setEnabled(not busy)
        self.volume.setEnabled(not busy and bool(self.info['audio']))
        self.save_btn.setEnabled(not busy and duration >= 1/30)
        self.cancel_btn.setText('Отменить сохранение' if busy else 'Закрыть')
        if not busy:
            self.status.setText(f'Останется {max(0, duration):.3f} с. ' + ('0% — без звука, 100% — исходная громкость.' if self.info['audio'] else 'В видео нет звуковой дорожки.'))

    def volume_changed(self, value):
        self.volume_label.setText(f'{value}%')
        # Preview has 6 dB of headroom so 200% is audible, not clamped at 100%.
        self.audio.setVolume(value/200)

    def position_changed(self, value):
        if not self.timeline.isSliderDown():
            self.timeline.setValue(value)
        seconds = value/1000
        self.time_label.setText(f'{int(seconds//60):02}:{seconds%60:06.3f}')
        if seconds >= self.end.value() and self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()

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

    def save(self):
        if self.worker or self.end.value()-self.start.value() < 1/30:
            return
        self.player.stop()
        self.player.setSource(QUrl())  # Release Windows file handles before replacement.
        self.worker = ExportThread(self.path, self.start.value(), self.end.value(), self.volume.value()/100, self.expected, self)
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
        if self.worker:
            self.close_after_cancel = True
            self.worker.job.cancel()
            self.status.setText('Отмена сохранения…')
            return
        self.player.stop()
        self.player.setSource(QUrl())
        super().reject()
