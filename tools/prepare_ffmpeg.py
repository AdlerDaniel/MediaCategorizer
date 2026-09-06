"""Stage the pinned FFmpeg Windows build for offline installations."""
import argparse
import os
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
VERSION = '8.1.2'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, help='Extracted ffmpeg-8.1.2-full_build directory from gyan.dev')
    args = parser.parse_args()
    target = ROOT / 'media_categorizer/assets/ffmpeg'
    if all((target / n).is_file() for n in ('ffmpeg.exe', 'ffprobe.exe')):
        return
    source = args.source
    if source is None:
        matches = list((Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/WinGet/Packages').glob('Gyan.FFmpeg_*/ffmpeg-' + VERSION + '-full_build'))
        source = matches[0] if matches else None
    if source is None:
        parser.error('Extract FFmpeg ' + VERSION + ' from https://www.gyan.dev/ffmpeg/builds/ and pass --source DIRECTORY.')
    target.mkdir(parents=True, exist_ok=True)
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        shutil.copy2(source / 'bin' / name, target / name)
    shutil.copy2(source / 'LICENSE', target / 'LICENSE.txt')

if __name__ == '__main__':
    main()
