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

if not exist "web\dist\index.html" (
    echo Mobile client build is missing: web\dist\index.html
    echo Run: cd web ^&^& corepack pnpm install ^&^& corepack pnpm build
    pause
    exit /b 1
)

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

set "LAN_IP=127.0.0.1"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } ^| Select-Object -First 1 -ExpandProperty IPAddress)"`) do set "LAN_IP=%%I"
echo.
echo Admin URL: http://%LAN_IP%:8501
echo Mobile client: http://%LAN_IP%:8765
echo Press Ctrl+C in this window to stop both services.
echo.

start "Bili Recipe Mobile" /b "%PYTHON_EXE%" -m uvicorn bili_recipe_notes.mobile_api:app --host 0.0.0.0 --port 8765 --no-access-log
"%PYTHON_EXE%" -m streamlit run bili_recipe_notes/ui.py --server.address=0.0.0.0 --server.port=8501 --browser.serverAddress=%LAN_IP%
taskkill /fi "WINDOWTITLE eq Bili Recipe Mobile" /t /f >nul 2>nul

echo.
echo UI server stopped.
pause
