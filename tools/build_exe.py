"""Build the Windows executable without DLLs from unrelated applications on PATH."""
import os
import shutil
import sys
from pathlib import Path


def main():
    project = Path(__file__).resolve().parents[1]
    os.chdir(project)
    if sys.platform == "win32":
        python = Path(sys.executable).resolve().parent
        windows = Path(os.environ["SystemRoot"])
        # PyInstaller resolves native dependencies using PATH. For example,
        # Poppler's ICU DLL has a different ABI than the Windows ICU used by Qt.
        os.environ["PATH"] = os.pathsep.join(map(str, (
            python, python / "Scripts", windows / "System32", windows,
        )))
    for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE.txt", "NOTICE.txt"):
        source = project / "media_categorizer/assets/ffmpeg" / name
        if not source.is_file():
            raise RuntimeError("Run python tools/prepare_ffmpeg.py before building.")
        destination = project / "dist/ffmpeg" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    from PyInstaller.__main__ import run
    run([
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "MediaCategorizer",
        "--add-data", "media_categorizer/assets/icons.json;media_categorizer/assets",
        "--add-data", "media_categorizer/assets/app.ico;media_categorizer/assets",
        "--add-data", "media_categorizer/assets/LUCIDE-LICENSE;media_categorizer/assets",
        "--icon", "media_categorizer/assets/app.ico",
        "--version-file", "installer/version.txt",
        "media_categorizer_v4_5.py",
    ])


if __name__ == "__main__":
    main()
