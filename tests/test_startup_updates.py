import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import base64
import hashlib
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QWidget
from media_categorizer.updates import StartupUpdates, UpdatePrompt


class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = {}
        self.active_file_operation = None
        self.file_operation_queue = []
        self.saved = False
    def _persist_settings(self):
        self.saved = True


class StartupUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.main = Main()
        self.main.show()
        self.controller = StartupUpdates(self.main)
        self.release = dict(version='99.0.0', filename='MediaCategorizer-Update-99.0.0.exe',
                            sha256=hashlib.sha256(b'fixture').hexdigest(), notes='New version')

    def tearDown(self):
        self.controller.stop()
        self.main.close()

    def settle(self, condition, timeout=5):
        deadline = time.monotonic()+timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(condition())

    def test_background_check_offers_new_release_once(self):
        with patch('media_categorizer.updates.latest_release', return_value=self.release) as check, patch.object(UpdatePrompt, 'exec', return_value=0) as offer:
            self.controller.start()
            self.controller.start()
            self.settle(lambda: offer.called)
            self.assertEqual(check.call_count, 1)
            self.assertEqual(offer.call_count, 1)

    def test_current_release_and_busy_window_do_not_interrupt(self):
        from media_categorizer.constants import APP_VERSION
        with patch.object(UpdatePrompt, 'exec', return_value=0) as offer:
            self.controller.checked(dict(self.release, version=APP_VERSION))
            self.assertFalse(offer.called)
            self.main.file_operation_queue = ['operation']
            self.controller.checked(self.release)
            self.assertFalse(offer.called)
            self.main.file_operation_queue.clear()
            self.controller.offer()
            self.assertEqual(offer.call_count, 1)

    def test_offline_or_closed_startup_is_quiet(self):
        finished = threading.Event()
        def fail(repository):
            finished.set()
            raise OSError('offline')
        with patch('media_categorizer.updates.latest_release', side_effect=fail), patch.object(UpdatePrompt, 'exec') as offer:
            self.controller.start()
            self.settle(finished.is_set)
            self.controller.stop()
            self.controller.checked(self.release)
            self.assertFalse(offer.called)

    def test_update_button_downloads_then_installs_without_second_click(self):
        dialog = UpdatePrompt(self.main, self.release)
        with patch('media_categorizer.updates.download_release', return_value='fixture.exe'), patch.object(dialog, 'install') as install:
            self.assertEqual(dialog.download_btn.text(), 'Обновить')
            dialog.download_btn.click()
            self.settle(lambda: dialog.worker is None)
            self.assertEqual(install.call_count, 1)
        dialog.reject()

    def test_failed_download_can_retry_and_never_installs(self):
        dialog = UpdatePrompt(self.main, self.release)
        with patch('media_categorizer.updates.download_release', side_effect=ValueError('checksum')), patch.object(dialog, 'install') as install:
            dialog.download_btn.click()
            self.settle(lambda: dialog.worker is None)
            self.assertFalse(install.called)
            self.assertTrue(dialog.download_btn.isEnabled())
        dialog.reject()

    def test_cancel_during_download_never_installs(self):
        gate = threading.Event()
        def download(*args):
            gate.wait(2)
            return 'fixture.exe'
        dialog = UpdatePrompt(self.main, self.release)
        with patch('media_categorizer.updates.download_release', side_effect=download), patch.object(dialog, 'install') as install:
            dialog.download_btn.click()
            dialog.reject()
            gate.set()
            self.settle(lambda: dialog.worker is None)
            self.assertFalse(install.called)

    def test_verified_installer_waits_for_exit_and_requests_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update's.exe"
            path.write_bytes(b'fixture')
            dialog = UpdatePrompt(self.main, self.release)
            dialog.downloaded = str(path)
            with patch('media_categorizer.updates.subprocess.Popen') as launch:
                dialog.install()
                command = launch.call_args.args[0][-1]
                self.assertEqual(launch.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
                self.assertFalse(any(key.startswith('_PYI_') for key in launch.call_args.kwargs['env']))
                script = base64.b64decode(command).decode('utf-16le')
                self.assertIn('Wait-Process -Id', script)
                self.assertIn('/SILENT', script)
                self.assertIn('/RESTARTAPP=1', script)
                self.assertIn("update''s.exe", script)
            self.assertFalse(self.main.isVisible())

    def test_changed_installer_never_runs(self):
        dialog = UpdatePrompt(self.main, self.release)
        dialog.downloaded = 'missing-update.exe'
        with patch('media_categorizer.updates.subprocess.Popen') as launch:
            dialog.install()
            self.assertFalse(launch.called)
        self.assertTrue(dialog.download_btn.isEnabled())
        dialog.reject()

    def test_icon_contains_windows_sizes(self):
        path = Path(__file__).resolve().parents[1] / 'media_categorizer/assets/app.ico'
        data = path.read_bytes()
        reserved, kind, count = struct.unpack_from('<HHH', data)
        self.assertEqual((reserved, kind, count), (0,1,7))
        self.assertEqual([data[6+i*16] or 256 for i in range(count)], [16,24,32,48,64,128,256])
