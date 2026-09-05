"""Build the Windows executable without DLLs from unrelated applications on PATH."""
import os
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
    from PyInstaller.__main__ import run
    run([
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "MediaCategorizer4_5", "media_categorizer_v4_5.py",
    ])


if __name__ == "__main__":
    main()
