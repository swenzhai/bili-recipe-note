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
   ! "$PYTHON_EXE" -c "import streamlit, yt_dlp, faster_whisper, pydantic, rich, reportlab, docx" >/dev/null 2>&1; then
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
echo "Press Ctrl+C in this window to stop the server."
echo

"$PYTHON_EXE" -m streamlit run bili_recipe_notes/ui.py \
    --server.address=127.0.0.1 \
    --browser.serverAddress=127.0.0.1 \
    --server.headless=false

echo
echo "UI server stopped."
pause_before_exit
