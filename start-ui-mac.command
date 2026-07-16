#!/bin/bash

cd "$(dirname "$0")" || exit 1

pause_before_exit() {
    echo
    read -r -p "Press Enter to close this window..."
}

if [ -n "${PYTHON:-}" ]; then
    BOOTSTRAP_PYTHON="$PYTHON"
elif [ -x "/opt/miniconda3/bin/python" ]; then
    BOOTSTRAP_PYTHON="/opt/miniconda3/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="$(command -v python3)"
else
    echo "Python 3 was not found. Install Python 3.10+ or Miniconda first."
    pause_before_exit
    exit 1
fi

echo "Starting Bili Recipe Notes UI..."
echo "Project: $(pwd)"
echo

if ! "$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required."
    echo "Detected: $($BOOTSTRAP_PYTHON --version 2>&1)"
    pause_before_exit
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "First launch: creating an isolated Python environment..."
    if ! "$BOOTSTRAP_PYTHON" -m venv .venv; then
        echo "Failed to create .venv with $BOOTSTRAP_PYTHON."
        pause_before_exit
        exit 1
    fi
fi

PYTHON_EXE=".venv/bin/python"
if command -v shasum >/dev/null 2>&1; then
    REQUIREMENTS_ID="$(shasum -a 256 requirements.txt | awk '{print $1}')"
else
    REQUIREMENTS_ID="$(cksum requirements.txt | awk '{print $1 ":" $2}')"
fi
STAMP_FILE=".venv/.bili-recipe-notes-requirements"
INSTALLED_REQUIREMENTS=""
if [ -f "$STAMP_FILE" ]; then
    INSTALLED_REQUIREMENTS="$(cat "$STAMP_FILE")"
fi

if [ "$INSTALLED_REQUIREMENTS" != "$REQUIREMENTS_ID" ] || \
   ! "$PYTHON_EXE" -c "import streamlit, yt_dlp, faster_whisper, pydantic, rich, reportlab, docx, fastapi, uvicorn, qrcode" >/dev/null 2>&1; then
    echo "Installing or updating app requirements (the first launch may take several minutes)..."
    if ! "$PYTHON_EXE" -m pip install -r requirements.txt; then
        echo
        echo "Failed to install requirements."
        pause_before_exit
        exit 1
    fi
    printf '%s\n' "$REQUIREMENTS_ID" > "$STAMP_FILE"
fi

if ! "$PYTHON_EXE" -c "import inspect; from yt_dlp.extractor.bilibili import BiliBiliIE; raise SystemExit(0 if '_dm_params' in inspect.getsource(BiliBiliIE._download_playinfo) else 1)" >/dev/null 2>&1; then
    echo "Reinstalling the pinned yt-dlp release for Bilibili support..."
    if ! "$PYTHON_EXE" -m pip install --force-reinstall "yt-dlp[default]==2026.7.4"; then
        echo
        echo "Failed to reinstall the pinned dependencies."
        pause_before_exit
        exit 1
    fi
fi

echo
echo "Browser URL: http://localhost:8501"
echo "Mobile API: http://0.0.0.0:8765 (use the pairing page to get the LAN address)"
echo "Press Ctrl+C in this window to stop the server."
echo

API_LOG=".venv/mobile-api.log"
API_PID=""
cleanup_api() {
    if [ -n "$API_PID" ] && kill -0 "$API_PID" >/dev/null 2>&1; then
        kill "$API_PID" >/dev/null 2>&1 || true
        wait "$API_PID" >/dev/null 2>&1 || true
    fi
}
trap cleanup_api EXIT INT TERM

"$PYTHON_EXE" -m uvicorn bili_recipe_notes.mobile_api:app \
    --host 0.0.0.0 \
    --port 8765 \
    --no-access-log >"$API_LOG" 2>&1 &
API_PID=$!

API_READY=0
for _ in $(seq 1 40); do
    if ! kill -0 "$API_PID" >/dev/null 2>&1; then
        break
    fi
    if "$PYTHON_EXE" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=1)" >/dev/null 2>&1; then
        API_READY=1
        break
    fi
    sleep 0.25
done
if [ "$API_READY" -ne 1 ]; then
    echo "Mobile API failed to start. Recent log output:"
    tail -n 30 "$API_LOG" 2>/dev/null || true
    cleanup_api
    pause_before_exit
    exit 1
fi

"$PYTHON_EXE" -m streamlit run bili_recipe_notes/ui.py \
    --server.address=127.0.0.1 \
    --browser.serverAddress=127.0.0.1 \
    --server.headless=false

cleanup_api
trap - EXIT INT TERM
echo
echo "UI server stopped."
pause_before_exit
