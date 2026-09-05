@echo off
cd /d "%~dp0"
python media_categorizer_v4_5.py
if errorlevel 1 pause
