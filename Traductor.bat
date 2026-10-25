@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
    start "" pythonw gui.py
) else (
    python traductor.py completo "%~1"
    pause
)
