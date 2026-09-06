"""Exercise the real app-to-updater handoff on an isolated test installation."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media_categorizer.constants import APP_VERSION
from verify_installer import value, KEY, UNINSTALL

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW

def child():
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtCore import QTimer, Qt
    from media_categorizer.updates import UpdatePrompt
    app = QApplication([])
    main = QWidget()
    main.settings = {}
    main.active_file_operation = None
    main.file_operation_queue = []
    main.setAttribute(Qt.WA_ShowWithoutActivating)
    main.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    main.show()
    path = ROOT / f'dist/MediaCategorizer-Update-{APP_VERSION}.exe'
    release = dict(version=APP_VERSION, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    dialog = UpdatePrompt(main, release)
    dialog.downloaded = str(path)
    QTimer.singleShot(100, dialog.install)
    return app.exec()

def processes(executable):
    escaped = str(executable).replace("'", "''")
    ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(ps), '-NoProfile', '-Command',
        "Get-CimInstance Win32_Process -Filter \"Name = 'MediaCategorizer.exe'\" | Where-Object { $_.ExecutablePath -eq '"+escaped+"' } | Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, creationflags=FLAGS, timeout=15)
    return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]

def close_test_windows(pids):
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    @callback
    def visit(hwnd, _):
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            ctypes.windll.user32.PostMessageW(hwnd, 0x10, 0, 0)
        return True
    ctypes.windll.user32.EnumWindows(visit, 0)

def main():
    assert value(KEY, 'InstallDir') is None and value(UNINSTALL, 'DisplayName') is None, 'Existing user installation; refusing test'
    with tempfile.TemporaryDirectory(prefix='mc-handoff-') as directory:
        base = Path(directory)
        target = base / 'Application'
        executable = target / 'MediaCategorizer.exe'
        env = dict(os.environ, APPDATA=str(base/'config'), QT_QPA_PLATFORM='windows')
        settings = base/'config/MediaCategorizer/settings_v4.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'theme':'light','categories':[{'name':'KEEP','action':'rename','shortcut':'1','destination':''}]}))
        journal = settings.with_name('processing_log_v4.jsonl')
        journal.write_text('{"action":"sentinel"}\n')
        baseline = ROOT/'dist/MediaCategorizer-Setup-6.0.0.exe'
        try:
            subprocess.run([str(baseline), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS', '/TASKS=', '/DIR='+str(target)], env=env, check=True, timeout=60)
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'], env=env, check=True, timeout=30, creationflags=FLAGS)
            deadline = time.monotonic()+90
            pids = []
            while time.monotonic() < deadline:
                if value(KEY, 'Version') == APP_VERSION:
                    pids = processes(executable)
                    if pids:
                        break
                time.sleep(1)
            assert pids, 'Updated app did not restart'
            assert hashlib.sha256(executable.read_bytes()).digest() == hashlib.sha256((ROOT/'dist/MediaCategorizer.exe').read_bytes()).digest()
            time.sleep(3)
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                pids = processes(executable)
                if not pids:
                    break
                close_test_windows(pids)
                time.sleep(.5)
            assert not processes(executable), 'Test app did not close normally'
            saved = json.loads(settings.read_text())
            assert saved['theme'] == 'light' and saved['categories'][0]['name'] == 'KEEP'
            assert journal.read_text() == '{"action":"sentinel"}\n'
            print('Real handoff: 6.0.0 -> '+APP_VERSION+'; restarted; payload, settings and journal verified', flush=True)
        finally:
            close_test_windows(processes(executable))
            uninstaller = target/'unins000.exe'
            if uninstaller.exists():
                subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], env=env, check=True, timeout=60)
        assert value(KEY, 'InstallDir') is None

if __name__ == '__main__':
    if '--child' in sys.argv:
        sys.exit(child())
    main()
