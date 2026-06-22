import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TOOLS_DIR = ROOT / "backend" / "tools"
DOUYIN_PARSER = TOOLS_DIR / "douyin_nocookie.py"
DOWNLOAD_DIR = ROOT / "downloads"
CACHE_DIR = ROOT / "cache"

MAX_SIZE_MB = 200
DOWNLOAD_TIMEOUT = 300
PROBE_TIMEOUT = 15  # 元数据探测超时，避免卡住「下载中」

SKILLS_DIR = ROOT / "skills"
DOUYIN_SKILL_DIR = SKILLS_DIR / "douyin-search-keyword"
DOUYIN_SEARCH_JS = DOUYIN_SKILL_DIR / "src" / "douyin" / "search-cli.js"
LOCAL_TENCENT_CHANNEL_CLI = SKILLS_DIR / "tencent-channel-cli"


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


def find_yt_dlp() -> str:
    candidates = [
        os.environ.get("YT_DLP"),
        str(ROOT / ".venv" / "bin" / "yt-dlp"),
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
YT_DLP_PATH = find_yt_dlp()
FFMPEG_PATH = find_ffmpeg()

PATH_ENV = (
    f"{ROOT / '.venv' / 'bin'}:"
    f"{LOCAL_TENCENT_CHANNEL_CLI / 'bin'}:"
    "/opt/homebrew/bin:/usr/local/bin"
)
