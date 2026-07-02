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
  url="https://npmmirror.com/mirrors/node/v${NODE_VERSION}/${archive}.${ext}"
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

setup_china_mirrors() {
  export UV_PYTHON_PREFERENCE="${UV_PYTHON_PREFERENCE:-only-managed}"
  export UV_INDEX_URL="${UV_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
  export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"
}

PYTHON_MIRRORS=(
  "https://registry.npmmirror.com/-/binary/python-build-standalone"
  "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"
)

venv_ready() {
  [ -f "$ROOT/.venv/bin/activate" ]
}

ensure_venv() {
  if venv_ready; then
    return 0
  fi

  echo "→ 创建 Python 虚拟环境（国内镜像）..."
  local mirror ver
  for mirror in "${PYTHON_MIRRORS[@]}"; do
    export UV_PYTHON_INSTALL_MIRROR="$mirror"
    echo "  镜像: $mirror"
    for ver in 3.11 3.12; do
      echo "  下载 Python ${ver} ..."
      if uv venv "$ROOT/.venv" --python "$ver" && venv_ready; then
        echo "✓ Python 虚拟环境已创建"
        return 0
      fi
    done
  done

  echo "✗ 无法创建虚拟环境（已尝试国内镜像）"
  exit 1
}

ensure_python_deps() {
  if ! venv_ready; then
    echo "✗ 虚拟环境不存在，无法安装 Python 依赖"
    exit 1
  fi

  echo "→ 安装 Python 依赖（国内 PyPI 镜像）..."
  uv pip install -q -r requirements.txt
  python -c "from backend.config import FFMPEG_CLI_PATH, FFMPEG_PATH; import sys; sys.exit(0 if (FFMPEG_CLI_PATH or FFMPEG_PATH) else 1)" \
    || echo "⚠ imageio-ffmpeg 未就绪，视频发帖可能失败"
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
  if ! (cd "$dir" && npm install "$pkg@1.0.7" --no-fund --no-audit -q); then
    if ! (cd "$dir" && npm install --no-fund --no-audit --omit=dev -q); then
      echo "⚠ tencent-channel-cli 安装失败，请手动在 skills/tencent-channel-cli 执行: npm install $pkg"
      return 0
    fi
  fi

  if [ ! -x "$bin" ]; then
    echo "⚠ 未找到 ${pkg} 二进制，请手动在 skills/tencent-channel-cli 执行: npm install $pkg"
  fi
}

ensure_uv
setup_china_mirrors
ensure_node
ensure_venv

# 激活 uv 虚拟环境
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"

ensure_python_deps
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
if [ -x "$ROOT/.venv/bin/ffmpeg" ]; then
  echo "✓ ffmpeg $ROOT/.venv/bin/ffmpeg"
elif python -c "from backend.config import FFMPEG_PATH; import sys; sys.exit(0 if FFMPEG_PATH else 1)" 2>/dev/null; then
  echo "✓ ffmpeg $(python -c "from backend.config import FFMPEG_PATH; print(FFMPEG_PATH)" 2>/dev/null)"
else
  echo "⚠ ffmpeg 未找到（请 uv pip install imageio-ffmpeg）"
fi
_tcli_bin=""
case "$(uname -s)" in
  Darwin|Linux)
    case "$(uname -m)" in
      x86_64|amd64) _tcli_arch=x64 ;;
      arm64|aarch64) _tcli_arch=arm64 ;;
    esac
    if [ -n "${_tcli_arch:-}" ]; then
      _tcli_plat=$([ "$(uname -s)" = Darwin ] && echo darwin || echo linux)
      _tcli_bin="$ROOT/skills/tencent-channel-cli/node_modules/tencent-channel-cli-${_tcli_plat}-${_tcli_arch}/bin/tencent-channel-cli"
    fi
    ;;
esac
if [ -n "$_tcli_bin" ] && [ -x "$_tcli_bin" ]; then
  echo "✓ tencent-channel-cli (${_tcli_plat}-${_tcli_arch})"
elif [ -x "$ROOT/skills/tencent-channel-cli/bin/tencent-channel-cli" ]; then
  echo "⚠ tencent-channel-cli wrapper 存在，但当前平台二进制未安装"
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
