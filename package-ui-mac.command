#!/bin/bash

cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
    PYTHON_EXE=".venv/bin/python"
else
    PYTHON_EXE="${PYTHON:-python3}"
fi

echo "Installing packaging requirements..."
if ! "$PYTHON_EXE" -m pip install -r requirements.txt; then
    echo "Failed to install requirements."
    read -r -p "Press Enter to close this window..."
    exit 1
fi

echo "Building macOS app..."
if ! "$PYTHON_EXE" -m PyInstaller --noconfirm --windowed --name "Bili Recipe Notes" bili_recipe_notes/ui_launcher.py; then
    echo "Build failed."
    read -r -p "Press Enter to close this window..."
    exit 1
fi

echo "Build complete: dist/Bili Recipe Notes.app"
read -r -p "Press Enter to close this window..."
