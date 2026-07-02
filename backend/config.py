import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TOOLS_DIR = ROOT / "backend" / "tools"
DOUYIN_PARSER = TOOLS_DIR / "douyin_nocookie.py"
DOWNLOAD_DIR = ROOT / "downloads"
CACHE_DIR = ROOT / "cache"

MAX_SIZE_MB = 200
DOWNLOAD_TIMEOUT = 300
PROBE_TIMEOUT = 90  # 元数据探测（含 B 站网页拉取重试）
BILI_PROBE_ATTEMPTS = 3
BILI_DOWNLOAD_ATTEMPTS = 3

SKILLS_DIR = ROOT / "skills"
DOUYIN_SKILL_DIR = SKILLS_DIR / "douyin-search-keyword"
DOUYIN_SEARCH_JS = DOUYIN_SKILL_DIR / "src" / "douyin" / "search-cli.js"
LOCAL_TENCENT_CHANNEL_CLI = SKILLS_DIR / "tencent-channel-cli"
LOCAL_NODE_DIR = ROOT / ".tools" / "node"


def find_cli() -> str:
    wrapper = LOCAL_TENCENT_CHANNEL_CLI / "bin" / "tencent-channel-cli"
    explicit = os.environ.get("TENCENT_CHANNEL_CLI")
    if explicit and Path(explicit).exists():
        return explicit
    return str(wrapper)


def find_cli_binary() -> str:
    """解析项目内 tencent-channel-cli 原生二进制。"""
    explicit = os.environ.get("TENCENT_CHANNEL_CLI_BINARY")
    if explicit and Path(explicit).exists():
        return explicit

    import platform as _pf

    system = _pf.system().lower()
    machine = _pf.machine().lower()
    plat = "darwin" if system == "darwin" else ("win32" if system == "windows" else system)
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    pkg = f"tencent-channel-cli-{plat}-{arch}"
    bin_name = "tencent-channel-cli.exe" if plat == "win32" else "tencent-channel-cli"

    candidate = LOCAL_TENCENT_CHANNEL_CLI / "node_modules" / pkg / "bin" / bin_name
    if candidate.exists():
        return str(candidate)

    wrapper = find_cli()
    if wrapper and Path(wrapper).exists():
        return wrapper
    return str(candidate)


def find_node() -> str:
    """优先项目内 .tools/node，其次系统 PATH。"""
    explicit = os.environ.get("NODE_BIN")
    if explicit and Path(explicit).exists():
        return explicit

    candidates: list[Path | str | None] = []
    if sys.platform == "win32":
        candidates.append(LOCAL_NODE_DIR / "node.exe")
    else:
        candidates.extend([
            LOCAL_NODE_DIR / "bin" / "node",
            Path("/opt/homebrew/bin/node"),
            Path("/usr/local/bin/node"),
        ])
    candidates.append(shutil.which("node"))

    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return "node"


def _venv_bin_dir() -> Path:
    return ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")


def build_path_env() -> str:
    node_bin = str(LOCAL_NODE_DIR if sys.platform == "win32" else LOCAL_NODE_DIR / "bin")
    parts = [
        str(_venv_bin_dir()),
        node_bin,
        str(LOCAL_TENCENT_CHANNEL_CLI / "bin"),
    ]
    if sys.platform == "darwin":
        parts.extend(["/opt/homebrew/bin", "/usr/local/bin"])
    return os.pathsep.join(p for p in parts if p)


def find_yt_dlp() -> str:
    candidates = [
        os.environ.get("YT_DLP"),
        str(_venv_bin_dir() / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")),
        "/opt/homebrew/bin/yt-dlp",
        "/usr/local/bin/yt-dlp",
        shutil.which("yt-dlp"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return "yt-dlp"


def find_ffmpeg() -> str | None:
    """优先 .venv 内 imageio-ffmpeg 自带二进制，其次环境变量/系统 PATH。"""
    explicit = os.environ.get("FFMPEG_PATH") or os.environ.get("IMAGEIO_FFMPEG_EXE")
    if explicit and Path(explicit).exists():
        return explicit
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:
        pass
    which = shutil.which("ffmpeg")
    return which if which else None


CLI_PATH = find_cli()
CLI_BINARY_PATH = find_cli_binary()
NODE_PATH = find_node()
YT_DLP_PATH = find_yt_dlp()
FFMPEG_PATH = find_ffmpeg()

PATH_ENV = build_path_env()
