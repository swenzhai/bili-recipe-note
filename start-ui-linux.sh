#!/usr/bin/env bash

set -u

cd "$(dirname "$0")" || exit 1

if [ -n "${PYTHON:-}" ]; then
    BOOTSTRAP_PYTHON="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    BOOTSTRAP_PYTHON="$(command -v python3)"
else
    echo "未找到 Python 3，请先安装 Python 3.10+ 和 python3-venv。"
    exit 1
fi

echo "正在启动 Bili Recipe Notes 局域网服务..."
echo "项目目录：$(pwd)"
echo

if ! "$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "需要 Python 3.10 或更新版本。"
    echo "当前版本：$($BOOTSTRAP_PYTHON --version 2>&1)"
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
   ! "$PYTHON_EXE" -c "import streamlit, yt_dlp, faster_whisper, pydantic, rich, reportlab, docx, fastapi, uvicorn, qrcode" >/dev/null 2>&1; then
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
echo "本机访问：http://127.0.0.1:8501"
if [ "$LAN_IP" != "127.0.0.1" ]; then
    echo "局域网访问：http://$LAN_IP:8501"
else
    echo "未能自动获取局域网 IP；可运行 'hostname -I' 查看后访问 http://<局域网IP>:8501"
fi
echo "手机同步 API：http://$LAN_IP:8765"
echo "仅限可信局域网使用，请勿把 8501 或 8765 端口暴露到公网。"
echo "按 Ctrl+C 可停止全部服务。"
echo

API_LOG=".venv/mobile-api.log"
UI_LOG=".venv/ui.log"
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
    echo "手机同步 API 启动失败，最近的日志如下："
    tail -n 30 "$API_LOG" 2>/dev/null || true
    exit 1
fi

export ARROW_DEFAULT_MEMORY_POOL="system"
UI_RESTART_COUNT=0
UI_MAX_RESTARTS=3
while true; do
    "$PYTHON_EXE" -m streamlit run bili_recipe_notes/ui.py \
        --server.address=0.0.0.0 \
        --server.port=8501 \
        --browser.serverAddress="$LAN_IP" \
        --server.headless=true 2>&1 | tee -a "$UI_LOG"
    UI_STATUS=${PIPESTATUS[0]}
    if [ "$UI_STATUS" -eq 0 ] || [ "$UI_STATUS" -eq 130 ] || [ "$UI_STATUS" -eq 143 ]; then
        break
    fi
    UI_RESTART_COUNT=$((UI_RESTART_COUNT + 1))
    if [ "$UI_RESTART_COUNT" -gt "$UI_MAX_RESTARTS" ]; then
        echo "UI 多次停止，最近的日志如下："
        tail -n 60 "$UI_LOG" 2>/dev/null || true
        break
    fi
    echo
    echo "UI 异常退出（状态码 $UI_STATUS），2 秒后重新启动..."
    echo "诊断日志：$(pwd)/$UI_LOG"
    sleep 2
done

cleanup_api
trap - EXIT INT TERM
echo
echo "服务已停止。"
