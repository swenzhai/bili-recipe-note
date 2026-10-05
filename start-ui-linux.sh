#!/usr/bin/env bash

set -u

cd "$(dirname "$0")" || exit 1

if [ -n "${PYTHON:-}" ]; then
    BOOTSTRAP_PYTHON="$PYTHON"
elif [ -x ".venv/bin/python" ] && \
     ".venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="$(command -v python3)"
else
    echo "未找到 Python 3，请先安装 Python 3.10+ 和 python3-venv。"
    exit 1
fi

echo "正在启动 Bili Recipe Notes 局域网服务..."
echo "项目目录：$(pwd)"
echo

if [ ! -f "web/dist/index.html" ]; then
    echo "未找到网页构建产物 web/dist/index.html。"
    echo "请先运行：cd web && corepack pnpm install && corepack pnpm build"
    exit 1
fi

if ! "$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "需要 Python 3.10 或更新版本。"
    echo "当前版本：$("$BOOTSTRAP_PYTHON" --version 2>&1)"
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    echo "首次启动：正在创建隔离的 Python 环境..."
    if ! "$BOOTSTRAP_PYTHON" -m venv .venv; then
        echo "创建 .venv 失败。Ubuntu/Debian 可先运行：sudo apt install python3-venv"
        exit 1
    fi
fi

PYTHON_EXE=".venv/bin/python"
if ! "$PYTHON_EXE" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "现有 .venv 无法运行 Python 3.10+。请备份该目录，再用有效的 Python 重建虚拟环境。"
    exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
    REQUIREMENTS_ID="$(sha256sum requirements.txt | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
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
    echo "正在安装或更新依赖，首次启动可能需要几分钟..."
    if ! "$PYTHON_EXE" -m pip install -r requirements.txt; then
        echo
        echo "安装依赖失败，请检查网络和上方错误信息。"
        exit 1
    fi
    printf '%s\n' "$REQUIREMENTS_ID" > "$STAMP_FILE"
fi

if ! "$PYTHON_EXE" -c "import inspect; from yt_dlp.extractor.bilibili import BiliBiliIE; raise SystemExit(0 if '_dm_params' in inspect.getsource(BiliBiliIE._download_playinfo) else 1)" >/dev/null 2>&1; then
    echo "正在重新安装项目锁定的 yt-dlp 版本..."
    if ! "$PYTHON_EXE" -m pip install --force-reinstall "yt-dlp[default]==2026.7.4"; then
        echo
        echo "重新安装 yt-dlp 失败。"
        exit 1
    fi
fi

detect_lan_ip() {
    local address=""
    if command -v ip >/dev/null 2>&1; then
        address="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (field = 1; field <= NF; field++) if ($field == "src") {print $(field + 1); exit}}')"
    fi
    if [ -z "$address" ] && command -v hostname >/dev/null 2>&1; then
        address="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    printf '%s\n' "${address:-127.0.0.1}"
}

LAN_IP="$(detect_lan_ip)"
echo
echo "本机访问：http://127.0.0.1:8765/admin/"
if [ "$LAN_IP" != "127.0.0.1" ]; then
    echo "局域网访问：http://$LAN_IP:8765/admin/"
else
    echo "未能自动获取局域网 IP；可运行 'hostname -I' 查看后访问 http://<局域网IP>:8765/admin/"
fi
echo "移动点餐客户端：http://$LAN_IP:8765"
echo "仅限可信局域网使用，请勿把 8765 端口暴露到公网。"
echo "按 Ctrl+C 可停止服务。"
echo

BILI_RECIPE_DATA_DIR="$("$PYTHON_EXE" -c 'from bili_recipe_notes.data_paths import data_root; print(data_root())')" || exit 1
export BILI_RECIPE_DATA_DIR
RECIPE_LOG_DIR="$BILI_RECIPE_DATA_DIR/logs"
mkdir -p "$RECIPE_LOG_DIR" || exit 1
UI_LOG="$RECIPE_LOG_DIR/admin-web.log"
echo "Data directory: $BILI_RECIPE_DATA_DIR"
"$PYTHON_EXE" -m bili_recipe_notes.ui_launcher --host 0.0.0.0 --port 8765 --no-browser 2>&1 | tee -a "$UI_LOG"
UI_STATUS=${PIPESTATUS[0]}
echo "Service stopped. Log: $UI_LOG"
exit "$UI_STATUS"
