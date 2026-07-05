@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

echo Installing packaging requirements...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

echo Building Windows executable...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --onefile --name BiliRecipeNotesUI bili_recipe_notes\ui_launcher.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo Build complete: dist\BiliRecipeNotesUI.exe
pause
