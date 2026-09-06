"""Reproducible Windows release: tests, app, setup, and in-place updater."""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from media_categorizer.constants import APP_VERSION

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iscc', help='Path to Inno Setup 6.7+ ISCC.exe')
    args = parser.parse_args()
    compiler = args.iscc or shutil.which('ISCC')
    for candidate in (ROOT / 'build/tooling/InnoSetup/ISCC.exe', Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Inno Setup 6/ISCC.exe'):
        if not compiler and candidate.is_file():
            compiler = str(candidate)
    if not compiler:
        parser.error('Install Inno Setup or supply --iscc PATH (https://jrsoftware.org/isdl.php).')
    for command in ([sys.executable, 'tools/prepare_ffmpeg.py'],
                    [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                    [sys.executable, 'tools/prepare_release.py'],
                    [sys.executable, 'tools/build_exe.py'],
                    [compiler, '/DAppVersion=' + APP_VERSION, 'installer/MediaCategorizer.iss'],
                    [compiler, '/DAppVersion=' + APP_VERSION, '/DUpdateOnly', 'installer/MediaCategorizer.iss']):
        subprocess.run(command, cwd=ROOT, check=True)
    names = ['MediaCategorizer.exe', 'MediaCategorizer-Setup-' + APP_VERSION + '.exe', 'MediaCategorizer-Update-' + APP_VERSION + '.exe']
    checksums = [hashlib.sha256((ROOT / 'dist' / name).read_bytes()).hexdigest() + '  ' + name for name in names]
    (ROOT / 'dist/SHA256SUMS.txt').write_text(chr(10).join(checksums) + chr(10))
    print('Release ready in', ROOT / 'dist')

if __name__ == '__main__':
    main()
