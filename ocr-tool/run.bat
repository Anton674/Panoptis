@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Распознавание текста с фото

if not exist ".venv\Scripts\python.exe" (
    echo Программа ещё не установлена. Сначала запустите install.bat
    pause
    exit /b 1
)

".venv\Scripts\python.exe" ocr.py --open-folder
pause
