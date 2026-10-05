@echo off
setlocal

cd /d "%~dp0"

if defined PYTHON (
    set "PYTHON_EXE=%PYTHON%"
) else if exist "%CD%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
) else (
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

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer is required. Check PYTHON or repair the existing .venv.
    "%PYTHON_EXE%" --version
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo Server dependencies are not installed. Installing requirements...
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
echo Admin URL: http://%LAN_IP%:8765/admin/
echo Mobile client: http://%LAN_IP%:8765
echo Press Ctrl+C in this window to stop the service.
echo.

set "RECIPE_RESOLVED_DATA_DIR="
for /f "usebackq delims=" %%D in (`""%PYTHON_EXE%" -c "from bili_recipe_notes.data_paths import data_root; print(data_root())""`) do set "RECIPE_RESOLVED_DATA_DIR=%%D"
if not defined RECIPE_RESOLVED_DATA_DIR exit /b 1
set "BILI_RECIPE_DATA_DIR=%RECIPE_RESOLVED_DATA_DIR%"
if not exist "%BILI_RECIPE_DATA_DIR%\logs" mkdir "%BILI_RECIPE_DATA_DIR%\logs"
if errorlevel 1 exit /b 1
echo Data directory: %BILI_RECIPE_DATA_DIR%
"%PYTHON_EXE%" -m bili_recipe_notes.ui_launcher --host 0.0.0.0 --port 8765
set "UI_STATUS=%ERRORLEVEL%"

echo.
echo UI server stopped.
pause
exit /b %UI_STATUS%
