"""GitHub Releases updater with SHA-256 verification before installation."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import urllib.request
from PySide6.QtCore import QThread, Signal, QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTextEdit, QProgressBar, QMessageBox
from .constants import APP_VERSION
from .settings import app_config_dir
from .ui import IconButton

DEFAULT_REPOSITORY = 'AdlerDaniel/MediaCategorizer'

def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Некорректная версия выпуска.')
    return tuple(map(int, match.groups()))

def request(url):
    if not url.startswith('https://'):
        raise ValueError('Источник обновления должен использовать HTTPS.')
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'MediaCategorizer/' + APP_VERSION, 'Accept':'application/vnd.github+json'}), timeout=30)

def latest_release(repository):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Укажите репозиторий в формате владелец/название.')
    with request('https://api.github.com/repos/' + repository + '/releases/latest') as response:
        data = json.loads(response.read(2*1024*1024))
    version = data['tag_name'].removeprefix('v')
    version_tuple(version)
    filename = 'MediaCategorizer-Update-' + version + '.exe'
    assets = {item['name']:item['browser_download_url'] for item in data.get('assets', [])}
    if filename not in assets or 'SHA256SUMS.txt' not in assets:
        raise ValueError('В выпуске нет полного пакета обновления или контрольных сумм.')
    with request(assets['SHA256SUMS.txt']) as response:
        sums = response.read(65536).decode('utf-8')
    checksum = next((line.split()[0].lower() for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1].lstrip('*') == filename), '')
    if not re.fullmatch('[a-f0-9]{64}', checksum):
        raise ValueError('Не найдена корректная контрольная сумма обновления.')
    return dict(version=version, filename=filename, url=assets[filename], sha256=checksum, notes=data.get('body') or '')

def download_release(release, progress=lambda value: None, cancelled=lambda: False):
    version_tuple(release['version'])
    directory = app_config_dir() / 'updates'
    directory.mkdir(exist_ok=True)
    filename = release['filename']
    if filename != 'MediaCategorizer-Update-' + release['version'] + '.exe':
        raise ValueError('Неверное имя файла обновления.')
    fd, temporary = tempfile.mkstemp(prefix='download-', suffix='.part', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as stream, request(release['url']) as response:
            total = int(response.headers.get('Content-Length') or 0)
            copied = 0
            digest = hashlib.sha256()
            while chunk := response.read(1024*1024):
                if cancelled():
                    raise ValueError('Загрузка отменена.')
                stream.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                progress(min(99, round(copied/total*100)) if total else 0)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled():
            raise ValueError('Загрузка отменена.')
        if digest.hexdigest() != release['sha256']:
            raise ValueError('Контрольная сумма не совпадает. Файл обновления удалён.')
        destination = directory / filename
        os.replace(temporary, destination)
        progress(100)
        return str(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)

class UpdateThread(QThread):
    progress = Signal(int)
    def __init__(self, repository, release=None, parent=None):
        super().__init__(parent)
        self.repository, self.release = repository, release
        self.result, self.error = None, None
    def run(self):
        try:
            self.result = download_release(self.release, self.progress.emit, self.isInterruptionRequested) if self.release else latest_release(self.repository)
        except Exception as exc:
            self.error = str(exc)

class UpdatesDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.worker = None
        self.auto_install = False
        self.cancel_pending = False
        self.release, self.downloaded = None, None
        self.setWindowTitle('О программе и обновления')
        self.resize(660, 470)
        self.repository = QLineEdit(main.settings.get('update_repository', DEFAULT_REPOSITORY))
        self.status = QLabel('Обновления проверяются при запуске. Здесь можно проверить повторно.')
        self.status.setWordWrap(True)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.check_btn = IconButton('history', 'Проверить обновления')
        self.download_btn = IconButton('check', 'Скачать обновление')
        self.install_btn = IconButton('check', 'Закрыть программу и установить')
        self.download_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.hide()
        self.check_btn.clicked.connect(self.check)
        self.download_btn.clicked.connect(self.download)
        self.install_btn.clicked.connect(self.install)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Media Categorizer · версия ' + APP_VERSION))
        layout.addWidget(QLabel('Источник обновлений GitHub (владелец/репозиторий)'))
        layout.addWidget(self.repository)
        layout.addWidget(self.status)
        layout.addWidget(self.notes)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        for widget in (self.check_btn, self.download_btn):
            row.addWidget(widget)
        layout.addLayout(row)
        layout.addWidget(self.install_btn)

    def start(self, release=None):
        self.cancel_pending = False
        self.check_btn.setEnabled(False)
        self.download_btn.setEnabled(False)
        self.install_btn.setEnabled(False)
        self.repository.setEnabled(False)
        self.worker = UpdateThread(self.repository.text().strip(), release, self)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self.done_work)
        self.worker.start()

    def check(self):
        self.release, self.downloaded = None, None
        self.status.setText('Проверка выпуска…')
        self.start()

    def download(self):
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText('Загрузка и проверка SHA-256…')
        self.start(self.release)

    def done_work(self):
        worker, self.worker = self.worker, None
        self.repository.setEnabled(True)
        self.check_btn.setEnabled(True)
        if self.cancel_pending:
            worker.deleteLater()
            super().reject()
            return
        if worker.error:
            self.status.setText('Не удалось проверить или загрузить обновление: ' + worker.error)
            self.download_btn.setEnabled(self.release is not None)
        elif worker.release:
            self.downloaded = worker.result
            self.install_btn.setEnabled(True)
            self.status.setText('Обновление скачано, контрольная сумма проверена.')
        else:
            self.release = worker.result
            newer = version_tuple(self.release['version']) > version_tuple(APP_VERSION)
            self.status.setText('Доступна версия ' + self.release['version'] if newer else 'Установлена актуальная версия.')
            self.notes.setPlainText(self.release['notes'])
            self.download_btn.setEnabled(newer)
            self.main.settings['update_repository'] = self.repository.text().strip()
            self.main._persist_settings()
        worker.deleteLater()
        if worker.release and not worker.error and self.auto_install:
            self.install()

    def install(self):
        if not self.downloaded or not self.release:
            return
        try:
            with Path(self.downloaded).open('rb') as stream:
                valid = hashlib.file_digest(stream, 'sha256').hexdigest() == self.release['sha256']
        except OSError:
            valid = False
        if not valid:
            self.status.setText('Файл изменился после загрузки. Скачайте обновление заново.')
            self.install_btn.setEnabled(False)
            self.download_btn.setEnabled(True)
            return
        if self.main.active_file_operation or self.main.file_operation_queue:
            QMessageBox.information(self, 'Обновление', 'Дождитесь завершения очереди операций.')
            return
        escaped = self.downloaded.replace("'", "''")
        script = f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; Start-Process -FilePath '{escaped}' -ArgumentList '/SILENT','/NORESTART','/RESTARTAPP=1'"
        command = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            subprocess.Popen([str(powershell), '-NoProfile', '-EncodedCommand', command], creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            self.status.setText('Не удалось запустить обновление: ' + str(exc))
            return
        self.accept()
        self.main.close()

    def reject(self):
        if self.worker:
            self.cancel_pending = True
            self.worker.requestInterruption()
            self.status.setText('Отмена загрузки… Окно закроется после завершения запроса.')
            return
        super().reject()


class UpdatePrompt(UpdatesDialog):
    """One click downloads, verifies, closes the app and starts the updater."""
    def __init__(self, main, release):
        super().__init__(main)
        self.release = release
        self.auto_install = True
        self.setWindowTitle('Доступно обновление Media Categorizer')
        self.status.setText('Доступна версия ' + release['version'] + '. Нажмите «Обновить»: программа скачает обновление, закроется и запустится после установки.')
        self.notes.setPlainText(release.get('notes', ''))
        self.repository.setReadOnly(True)
        self.check_btn.setText('Позже')
        self.check_btn.clicked.disconnect()
        self.check_btn.clicked.connect(self.reject)
        self.download_btn.setText('Обновить')
        self.download_btn.setProperty('primary', True)
        self.download_btn.setEnabled(True)
        self.install_btn.hide()

    def start(self, release=None):
        super().start(release)
        self.check_btn.setText('Отменить')
        self.check_btn.setEnabled(True)


class StartupUpdates(QObject):
    ready = Signal(object)

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.pending = None
        self.started = False
        self.cancelled = threading.Event()
        self.ready.connect(self.checked)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.offer)

    def start(self):
        if self.started or self.cancelled.is_set():
            return
        self.started = True
        repository = self.main.settings.get('update_repository', DEFAULT_REPOSITORY)
        def check():
            try:
                release = latest_release(repository)
                if not self.cancelled.is_set():
                    self.ready.emit(release)
            except Exception:
                pass  # Offline startup must remain quiet and responsive.
        threading.Thread(target=check, name='startup-update-check', daemon=True).start()

    def checked(self, release):
        if self.cancelled.is_set() or version_tuple(release['version']) <= version_tuple(APP_VERSION):
            return
        self.pending = release
        self.timer.start()
        self.offer()

    def offer(self):
        if self.cancelled.is_set() or not self.pending:
            return
        if (not self.main.isVisible() or QApplication.activeModalWidget() is not None
                or self.main.active_file_operation or self.main.file_operation_queue):
            return
        self.timer.stop()
        release, self.pending = self.pending, None
        UpdatePrompt(self.main, release).exec()

    def stop(self):
        self.cancelled.set()
        self.timer.stop()
        self.pending = None
