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

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  echo "→ 未检测到 uv，正在安装..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    echo "✗ uv 安装失败，请手动安装: https://docs.astral.sh/uv/"
    exit 1
  fi
}

NODE_VERSION="${NODE_VERSION:-20.18.3}"
TOOLS_NODE="$ROOT/.tools/node"

prepend_node_path() {
  if [ -d "$TOOLS_NODE/bin" ]; then
    export PATH="$TOOLS_NODE/bin:$PATH"
  fi
}

ensure_node() {
  prepend_node_path
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    return 0
  fi

  echo "→ 未检测到 Node.js，正在安装到 .tools/node (v${NODE_VERSION})..."

  local os arch ext archive url tmpdir
  case "$(uname -s)" in
    Darwin) os=darwin; ext=tar.gz ;;
    Linux) os=linux; ext=tar.xz ;;
    *)
      echo "✗ 无法自动安装 Node.js，请手动安装: https://nodejs.org/"
      exit 1
      ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    arm64|aarch64) arch=arm64 ;;
    *)
      echo "✗ 不支持的 CPU 架构: $(uname -m)"
      exit 1
      ;;
  esac

  if ! command -v curl >/dev/null 2>&1; then
    echo "✗ 需要 curl 以下载 Node.js"
    exit 1
  fi

  archive="node-v${NODE_VERSION}-${os}-${arch}"
  url="https://nodejs.org/dist/v${NODE_VERSION}/${archive}.${ext}"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  curl -fsSL "$url" -o "$tmpdir/node.${ext}"
  mkdir -p "$ROOT/.tools"
  rm -rf "$TOOLS_NODE"
  if [ "$ext" = "tar.xz" ]; then
    tar -xJf "$tmpdir/node.${ext}" -C "$ROOT/.tools"
  else
    tar -xzf "$tmpdir/node.${ext}" -C "$ROOT/.tools"
  fi
  mv "$ROOT/.tools/$archive" "$TOOLS_NODE"
  export PATH="$TOOLS_NODE/bin:$PATH"

  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "✗ Node.js 安装失败"
    exit 1
  fi
  echo "✓ node $(node --version 2>/dev/null)"
}

ensure_tencent_cli() {
  local dir="$ROOT/skills/tencent-channel-cli"
  [ -d "$dir" ] || return 0
  command -v node >/dev/null 2>&1 || return 0
  command -v npm >/dev/null 2>&1 || return 0

  local plat arch pkg bin
  case "$(uname -s)" in
    Darwin) plat=darwin ;;
    Linux) plat=linux ;;
    *) return 0 ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) return 0 ;;
  esac

  pkg="tencent-channel-cli-${plat}-${arch}"
  bin="$dir/node_modules/$pkg/bin/tencent-channel-cli"
  if [ -x "$bin" ]; then
    return 0
  fi

  echo "→ 安装 tencent-channel-cli (${pkg})..."
  (cd "$dir" && npm install --no-fund --no-audit --omit=dev -q) \
    || (cd "$dir" && npm install "$pkg" --no-fund --no-audit -q) \
    || echo "⚠ tencent-channel-cli 安装失败，请手动在 skills/tencent-channel-cli 执行 npm install"
}

ensure_uv
ensure_node

# 未初始化则自动创建
if [ ! -d "$ROOT/.venv" ]; then
  echo "→ 创建 uv 虚拟环境..."
  uv venv "$ROOT/.venv" --python 3.11 2>/dev/null || uv venv "$ROOT/.venv"
fi

# 激活 uv 虚拟环境
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

# 同步依赖（含 yt-dlp、imageio-ffmpeg 等）
echo "→ 安装 Python 依赖..."
uv pip install -q -r requirements.txt

ensure_tencent_cli

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
