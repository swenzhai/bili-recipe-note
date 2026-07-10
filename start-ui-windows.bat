@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

echo Starting Bili Recipe Notes UI...
echo Project: %CD%
echo.

"%PYTHON_EXE%" -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo Streamlit is not installed. Installing requirements...
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Failed to install requirements.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" -c "import inspect; from yt_dlp.extractor.bilibili import BiliBiliIE; raise SystemExit(0 if '_dm_params' in inspect.getsource(BiliBiliIE._download_playinfo) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Reinstalling the pinned yt-dlp release for Bilibili support...
    "%PYTHON_EXE%" -m pip install --force-reinstall -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Failed to reinstall the pinned dependencies.
        pause
        exit /b 1
    )
)

echo.
echo Browser URL: http://localhost:8501
echo Press Ctrl+C in this window to stop the server.
echo.

"%PYTHON_EXE%" -m streamlit run bili_recipe_notes/ui.py --server.address=127.0.0.1 --browser.serverAddress=127.0.0.1

echo.
echo UI server stopped.
pause
