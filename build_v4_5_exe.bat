@echo off
setlocal
cd /d "%~dp0"

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements_v4_5.txt
if errorlevel 1 goto :error

python -m pip install pyinstaller
if errorlevel 1 goto :error

echo Building MediaCategorizer4_5.exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name MediaCategorizer4_5 media_categorizer_v4_5.py
if errorlevel 1 goto :error

echo.
echo Done: dist\MediaCategorizer4_5.exe
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
