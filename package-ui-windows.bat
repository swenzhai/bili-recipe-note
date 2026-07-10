@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

echo Installing packaging requirements...
"%PYTHON_EXE%" -m pip install -r requirements-dev.txt
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

echo Building Windows executable...
"%PYTHON_EXE%" -m PyInstaller --clean --noconfirm bili-recipe-notes-ui.spec
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo Build complete: dist\BiliRecipeNotesUI.exe
pause
