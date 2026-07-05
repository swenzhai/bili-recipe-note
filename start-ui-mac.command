#!/bin/bash

cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
    PYTHON_EXE=".venv/bin/python"
else
    PYTHON_EXE="${PYTHON:-python3}"
fi

echo "Starting Bili Recipe Notes UI..."
echo "Project: $(pwd)"
echo

if ! "$PYTHON_EXE" -c "import streamlit" >/dev/null 2>&1; then
    echo "Streamlit is not installed. Installing requirements..."
    if ! "$PYTHON_EXE" -m pip install -r requirements.txt; then
        echo
        echo "Failed to install requirements."
        read -r -p "Press Enter to close this window..."
        exit 1
    fi
fi

if ! "$PYTHON_EXE" -c "import inspect; from yt_dlp.extractor.bilibili import BiliBiliIE; raise SystemExit(0 if '_dm_params' in inspect.getsource(BiliBiliIE._download_playinfo) else 1)" >/dev/null 2>&1; then
    echo "Updating yt-dlp for recent Bilibili changes..."
    if ! "$PYTHON_EXE" -m pip install --force-reinstall "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz"; then
        echo
        echo "Failed to update yt-dlp."
        read -r -p "Press Enter to close this window..."
        exit 1
    fi
fi

echo
echo "Browser URL: http://localhost:8501"
echo "Press Ctrl+C in this window to stop the server."
echo

"$PYTHON_EXE" -m streamlit run bili_recipe_notes/ui.py

echo
echo "UI server stopped."
read -r -p "Press Enter to close this window..."
