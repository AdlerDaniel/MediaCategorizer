@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

python -m unittest discover -s tests -v
if errorlevel 1 goto :error

python tools/prepare_ffmpeg.py
if errorlevel 1 goto :error

python tools/prepare_release.py
if errorlevel 1 goto :error

python tools/build_exe.py
if errorlevel 1 goto :error

echo Done: dist\MediaCategorizer.exe
pause
exit /b 0

:error
echo Build failed.
pause
exit /b 1
