"""Real install/update/uninstall smoke. Refuses an existing user installation."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import winreg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from media_categorizer.constants import APP_VERSION
ROOT = Path(__file__).resolve().parents[1]
KEY = 'Software/MediaCategorizer'.replace('/', chr(92))
UNINSTALL = 'Software/Microsoft/Windows/CurrentVersion/Uninstall/MediaCategorizer_is1'.replace('/', chr(92))
VIEW = winreg.KEY_WOW64_64KEY

def value(key, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | VIEW) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except FileNotFoundError:
        return None

def set_version(version):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE | VIEW) as handle:
        winreg.SetValueEx(handle, 'Version', 0, winreg.REG_SZ, version)

def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, help='Older Setup EXE to test a real version upgrade')
    args = parser.parse_args()
    assert value(KEY, 'InstallDir') is None and value(UNINSTALL, 'DisplayName') is None, 'Existing installation: use a clean Windows user for this test.'
    report = {}
    logs = ROOT / 'build/installer-qa'
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='MediaCategorizer-install-') as temporary:
        base = Path(temporary)
        target = base / 'Application'
        env = dict(os.environ, APPDATA=str(base / 'config'))
        settings = base / 'config/MediaCategorizer/settings_v4.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'theme':'light', 'categories':[{'name':'My category'}]}))
        journal = settings.with_name('processing_log_v4.jsonl')
        journal.write_text('{"action":"sentinel"}\n')
        original = settings.read_bytes(), journal.read_bytes()
        setup = args.baseline.resolve() if args.baseline else ROOT / ('dist/MediaCategorizer-Setup-' + APP_VERSION + '.exe')
        update = ROOT / ('dist/MediaCategorizer-Update-' + APP_VERSION + '.exe')
        def execute(exe, name, *args, success=True):
            result = subprocess.run([str(exe), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(logs / (name+'.log')), *args], env=env, timeout=60)
            assert (result.returncode == 0) == success, (name, result.returncode)
            report[name] = result.returncode
        try:
            execute(update, 'missing-install-blocked', success=False)
            execute(setup, 'install', '/DIR=' + str(target), '/NOICONS', '/TASKS=')
            assert Path(value(KEY, 'InstallDir')) == target
            assert value(UNINSTALL, 'DisplayName').startswith('Media Categorizer')
            executable = target / 'MediaCategorizer.exe'
            expected = digest(ROOT / 'dist/MediaCategorizer.exe')
            if args.baseline:
                assert value(KEY, 'Version') != APP_VERSION
                assert digest(executable) != expected
                report['baseline_version'] = value(KEY, 'Version')
            else:
                assert digest(executable) == expected
                # Simulate an older payload and version, then prove replacement.
                executable.write_bytes(b'old release fixture')
                set_version('4.9.0')
            execute(update, 'update', '/DIR=' + str(base / 'WrongFolder'))
            assert digest(executable) == expected
            assert not (base / 'WrongFolder').exists()
            assert value(KEY, 'Version') == APP_VERSION
            assert original == (settings.read_bytes(), journal.read_bytes())
            assert len(list(target.glob('unins*.exe'))) == 1
            powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            smoke = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tools/verify_exe.ps1'), '-Executable', str(executable)], capture_output=True, text=True, timeout=60)
            assert smoke.returncode == 0, smoke.stdout + smoke.stderr
            report['installed_app_launch'] = json.loads(smoke.stdout.strip())
            execute(update, 'repeat-update')
            set_version('99.0.0')
            execute(update, 'downgrade-blocked', success=False)
            assert digest(executable) == expected
            set_version(APP_VERSION)
            ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
            guard = ctypes.windll.kernel32.CreateMutexW(None, False, 'MediaCategorizer.Running')
            try:
                execute(update, 'running-app-blocked', success=False)
            finally:
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(guard))
            report['settings_and_journal_preserved'] = original == (settings.read_bytes(), journal.read_bytes())
            report['single_uninstaller'] = len(list(target.glob('unins*.exe'))) == 1
        finally:
            uninstaller = target / 'unins000.exe'
            if uninstaller.exists():
                execute(uninstaller, 'uninstall')
        assert value(KEY, 'InstallDir') is None
        assert value(UNINSTALL, 'DisplayName') is None
        assert not (target / 'MediaCategorizer.exe').exists()
        assert original == (settings.read_bytes(), journal.read_bytes())
    (logs / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))

if __name__ == '__main__':
    main()
