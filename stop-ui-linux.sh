#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd -- "$SCRIPT_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "此关闭脚本适用于 Linux。" >&2
    exit 1
fi

STOP_PYTHON=""
for candidate in "${PYTHON:-}" "$SCRIPT_DIR/.venv/bin/python" python3; do
    if [[ -n "$candidate" ]] && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
        STOP_PYTHON="$candidate"
        break
    fi
done
if [[ -z "$STOP_PYTHON" ]]; then
    echo "未找到可运行的 Python 3.10+。" >&2
    exit 1
fi

exec "$STOP_PYTHON" "$SCRIPT_DIR/bili_recipe_notes/stop_ui.py" "$@"
