#!/usr/bin/env bash
# 腾讯频道发帖 Web 工具 — 一键启动
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"

# PATH
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
export PATH="$HOME/Library/Application Support/QClaw/openclaw/config/bin:$PATH"
export PATH="$HOME/Library/Application Support/QClaw/npm-global/bin:$PATH"

echo "========================================"
echo "  腾讯频道发帖工具"
echo "========================================"

# 未初始化则自动创建
if [ ! -d "$ROOT/.venv" ]; then
  echo "→ 创建 uv 虚拟环境..."
  uv venv "$ROOT/.venv" --python 3.11 2>/dev/null || uv venv "$ROOT/.venv"
fi

# 激活 uv 虚拟环境
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

# 同步依赖（含 yt-dlp，优先用 .venv 内最新版）
uv pip install -q -r requirements.txt 2>/dev/null || true

check_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "✓ $1"
  else
    echo "⚠ $1 未安装"
  fi
}

echo "→ 检查工具..."
YT_DLP_BIN="$ROOT/.venv/bin/yt-dlp"
if [ -x "$YT_DLP_BIN" ]; then
  echo "✓ yt-dlp $("$YT_DLP_BIN" --version 2>/dev/null | head -1)"
else
  check_cmd yt-dlp
fi
if python3 -c "from backend.config import FFMPEG_PATH; import sys; sys.exit(0 if FFMPEG_PATH else 1)" 2>/dev/null; then
  echo "✓ ffmpeg $(python3 -c "from backend.config import FFMPEG_PATH; print(FFMPEG_PATH)" 2>/dev/null)"
else
  echo "⚠ ffmpeg 未找到（请 uv pip install imageio-ffmpeg）"
fi
if [ -x "$ROOT/skills/tencent-channel-cli/bin/tencent-channel-cli" ]; then
  echo "✓ tencent-channel-cli (skills/)"
else
  echo "⚠ skills/tencent-channel-cli 未找到"
fi
if command -v node >/dev/null 2>&1; then
  echo "✓ node $(node --version 2>/dev/null)"
else
  echo "⚠ node 未安装（抖音搜索需要）"
fi

if [ ! -f "$ROOT/config/config.json" ]; then
  if [ -f "$ROOT/config/config.template.json" ]; then
    cp "$ROOT/config/config.template.json" "$ROOT/config/config.json"
    echo "✓ 已从 config.template.json 创建 config/config.json，请填写 Token 与 Cookie"
  else
    echo "⚠ 未找到 config/config.template.json，无法自动创建 config.json"
  fi
fi

# 释放占用端口
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "→ 端口 $PORT 已被占用，正在释放..."
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

mkdir -p "$ROOT/downloads" "$ROOT/cache"

if [ "$HOST" = "0.0.0.0" ] || [ "$HOST" = "::" ]; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"
  URL="http://127.0.0.1:${PORT}"
  if [ -n "$LAN_IP" ]; then
    URL="$URL  （局域网: http://${LAN_IP}:${PORT}）"
  else
    URL="$URL  （已监听所有网卡，可用本机 IP 访问）"
  fi
else
  URL="http://${HOST}:${PORT}"
fi
echo ""
echo "🚀 启动服务: $URL"
echo "   按 Ctrl+C 停止"
echo ""

if [[ "$(uname)" == "Darwin" ]] && [[ "${OPEN_BROWSER:-1}" == "1" ]]; then
  (sleep 1.5 && open "http://127.0.0.1:${PORT}") &
fi

exec python -m uvicorn backend.main:app --host "$HOST" --port "$PORT"
