#!/bin/bash

cd "$(dirname "$0")" || exit 1

pause_before_exit() {
    echo
    read -r -p "Press Enter to close this window..."
}

if [ -n "${PYTHON:-}" ]; then
    BOOTSTRAP_PYTHON="$PYTHON"
elif [ -x ".venv/bin/python" ] && \
     ".venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON=".venv/bin/python"
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

if [ ! -f "web/dist/index.html" ]; then
    echo "Web client build is missing: web/dist/index.html"
    echo "Run: cd web && corepack pnpm install && corepack pnpm build"
    pause_before_exit
    exit 1
fi

if ! "$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required."
    echo "Detected: $("$BOOTSTRAP_PYTHON" --version 2>&1)"
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
if ! "$PYTHON_EXE" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "The existing .venv cannot run Python 3.10+. Back it up and recreate it with a supported Python."
    pause_before_exit
    exit 1
fi
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
   ! "$PYTHON_EXE" -c "import streamlit, streamlit_cropper, yt_dlp, faster_whisper, pydantic, rich, reportlab, docx, fastapi, uvicorn, qrcode" >/dev/null 2>&1; then
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

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"
echo
echo "Admin URL: http://$LAN_IP:8765/admin/"
echo "Mobile client: http://$LAN_IP:8765"
echo "Press Ctrl+C in this window to stop the server."
echo

BILI_RECIPE_DATA_DIR="$("$PYTHON_EXE" -c 'from bili_recipe_notes.data_paths import data_root; print(data_root())')" || exit 1
export BILI_RECIPE_DATA_DIR
RECIPE_LOG_DIR="$BILI_RECIPE_DATA_DIR/logs"
mkdir -p "$RECIPE_LOG_DIR" || exit 1
UI_LOG="$RECIPE_LOG_DIR/admin-web.log"
echo "Data directory: $BILI_RECIPE_DATA_DIR"
"$PYTHON_EXE" -m bili_recipe_notes.ui_launcher --host 0.0.0.0 --port 8765 2>&1 | tee -a "$UI_LOG"
UI_STATUS=${PIPESTATUS[0]}
echo "Service stopped. Log: $UI_LOG"
pause_before_exit
exit "$UI_STATUS"
