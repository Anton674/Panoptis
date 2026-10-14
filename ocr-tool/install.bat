@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Установка: распознавание текста с фото

echo ============================================================
echo   Установка программы распознавания текста
echo   Нужен интернет. Займёт 5-15 минут, окно не закрывайте.
echo ============================================================
echo.

rem --- 1. Ищем подходящий Python (3.10 - 3.13) ---
call :find_python
if not defined PY (
    echo Python не найден. Устанавливаю Python 3.12...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    call :find_python
)
if not defined PY (
    echo.
    echo [ОШИБКА] Не удалось установить Python автоматически.
    echo Скачайте Python 3.12 с https://www.python.org/downloads/
    echo При установке поставьте галочку "Add python.exe to PATH",
    echo затем запустите install.bat ещё раз.
    goto :fail
)
echo Найден Python: !PY!
echo.

rem --- 2. Виртуальное окружение ---
if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение...
    !PY! -m venv .venv || goto :fail
)
set "VPY=%~dp0.venv\Scripts\python.exe"

rem --- 3. Библиотеки ---
echo Обновляю pip...
"%VPY%" -m pip install --upgrade pip || goto :fail
echo.
echo Устанавливаю PyTorch (версия для процессора, около 200 МБ)...
"%VPY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :fail
echo.
echo Устанавливаю EasyOCR и остальные библиотеки...
"%VPY%" -m pip install -r requirements.txt || goto :fail
echo.

rem --- 4. Модели распознавания и ярлыки ---
echo Скачиваю модели распознавания (русский + английский)...
"%VPY%" ocr.py --download-models || goto :fail
echo.
"%VPY%" ocr.py --make-shortcuts

echo.
echo ============================================================
echo   Готово!
echo   На рабочем столе появились ярлыки:
echo     "Распознать текст с фото" - запускает программу
echo     "Фото для распознавания"  - папка, куда класть фото
echo ============================================================
pause
exit /b 0

:find_python
set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    for %%V in (312 313 311 310) do (
        if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
            set PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        )
    )
)
if not defined PY (
    python -c "import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 13))" >nul 2>&1 && set "PY=python"
)
exit /b 0

:fail
echo.
echo [ОШИБКА] Установка не завершена. Сфотографируйте это окно и отправьте мне.
pause
exit /b 1
