import os
import shutil
import sys
from pathlib import Path

MAX_SIZE_MB = 200
# 下载 worker 外层 kill 超时（唯一配置源，+30s 内部 deadline 缓冲）
DOWNLOAD_WORKER_TIMEOUT = {
    "douyin": 120,
    "bili": 300,
}
DOWNLOAD_WORKER_KILL_BUFFER = 30  # 子进程内部 deadline = 外层 - buffer
BILI_API_TIMEOUT = 12
DOUYIN_CURL_MAX_TIME = 15
# 兼容旧引用（yt-dlp 单次下载上限）
DOWNLOAD_TIMEOUT = 180


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _app_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bundle_root() -> Path:
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return _app_root()


def _first_existing(*candidates: Path) -> Path:
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


ROOT = _app_root()
BUNDLE_ROOT = _bundle_root()

STATIC_DIR = _first_existing(BUNDLE_ROOT / "static", ROOT / "static")
CONFIG_DIR = ROOT / "config"
TOOLS_DIR = _first_existing(BUNDLE_ROOT / "backend" / "tools", ROOT / "backend" / "tools")
DOUYIN_PARSER = TOOLS_DIR / "douyin_nocookie.py"
DOWNLOAD_DIR = ROOT / "downloads"
CACHE_DIR = ROOT / "cache"

SKILLS_DIR = _first_existing(ROOT / "runtime" / "skills", BUNDLE_ROOT / "skills", ROOT / "skills")
DOUYIN_SKILL_DIR = SKILLS_DIR / "douyin-search-keyword"
DOUYIN_SEARCH_JS = DOUYIN_SKILL_DIR / "src" / "douyin" / "search-cli.js"
LOCAL_TENCENT_CHANNEL_CLI = SKILLS_DIR / "tencent-channel-cli"
LOCAL_NODE_DIR = _first_existing(
    ROOT / "runtime" / "node",
    ROOT / ".tools" / "node",
    BUNDLE_ROOT / "runtime" / "node",
)


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
    if system == "windows":
        plat, arch = "win32", "x64"
    else:
        plat = "darwin" if system == "darwin" else system
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
    """优先项目内 runtime/node 或 .tools/node，其次系统 PATH。"""
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


def _tools_bin_dir() -> Path:
    if is_frozen():
        runtime_bin = ROOT / "runtime" / "bin"
        runtime_bin.mkdir(parents=True, exist_ok=True)
        return runtime_bin
    return ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")


def build_path_env(*, ffmpeg_path: str | None = None) -> str:
    node_bin = str(LOCAL_NODE_DIR if sys.platform == "win32" else LOCAL_NODE_DIR / "bin")
    parts = [
        str(_tools_bin_dir()),
        node_bin,
        str(LOCAL_TENCENT_CHANNEL_CLI / "bin"),
    ]
    if ffmpeg_path:
        parts.insert(1, str(Path(ffmpeg_path).parent))
    if sys.platform == "darwin":
        parts.extend(["/opt/homebrew/bin", "/usr/local/bin"])
    return os.pathsep.join(p for p in parts if p)


def _ensure_ytdlp_shim() -> None:
    if not is_frozen():
        return

    bin_dir = _tools_bin_dir()
    if sys.platform == "win32":
        shim = bin_dir / "yt-dlp.cmd"
        if shim.exists():
            return
        shim.write_text(f'@"{sys.executable}" -m yt_dlp %*\r\n', encoding="utf-8")
        return

    shim = bin_dir / "yt-dlp"
    if shim.exists():
        return
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m yt_dlp "$@"\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | 0o111)


def find_yt_dlp() -> str:
    _ensure_ytdlp_shim()

    candidates = [
        os.environ.get("YT_DLP"),
        str(_tools_bin_dir() / ("yt-dlp.cmd" if sys.platform == "win32" else "yt-dlp")),
        str(_tools_bin_dir() / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")),
        "/opt/homebrew/bin/yt-dlp",
        "/usr/local/bin/yt-dlp",
        shutil.which("yt-dlp"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return "yt-dlp"


def find_ffmpeg() -> str | None:
    """优先 imageio-ffmpeg 自带二进制，其次环境变量/系统 PATH。"""
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


def _ensure_ffmpeg_shim(ffmpeg_path: str) -> str | None:
    """在 runtime/bin 或 .venv 内创建 ffmpeg，供 tencent-channel-cli 通过 PATH 查找。"""
    src = Path(ffmpeg_path)
    if not src.exists():
        return None

    shim_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    shim = _tools_bin_dir() / shim_name
    if shim.exists():
        try:
            if shim.resolve() == src.resolve():
                return str(shim)
        except OSError:
            pass
        try:
            shim.unlink()
        except OSError:
            return None

    if sys.platform == "win32":
        try:
            shutil.copy2(src, shim)
            return str(shim)
        except OSError:
            return None

    try:
        shim.symlink_to(src.resolve())
        return str(shim)
    except OSError:
        pass

    try:
        shutil.copy2(src, shim)
        shim.chmod(shim.stat().st_mode | 0o111)
        return str(shim)
    except OSError:
        return None


def init_portable_dirs() -> None:
    """便携版首次启动：创建可写目录并复制配置模板。"""
    if not is_frozen():
        return

    for name in ("config", "downloads", "cache", "runtime/bin"):
        (ROOT / name).mkdir(parents=True, exist_ok=True)

    template_src = BUNDLE_ROOT / "config" / "config.template.json"
    template_dst = CONFIG_DIR / "config.template.json"
    if template_src.exists() and not template_dst.exists():
        shutil.copy2(template_src, template_dst)

    config_file = CONFIG_DIR / "config.json"
    if not config_file.exists() and template_dst.exists():
        shutil.copy2(template_dst, config_file)


if is_frozen():
    init_portable_dirs()


CLI_PATH = find_cli()
CLI_BINARY_PATH = find_cli_binary()
NODE_PATH = find_node()
YT_DLP_PATH = find_yt_dlp()
FFMPEG_PATH = find_ffmpeg()
FFMPEG_CLI_PATH = _ensure_ffmpeg_shim(FFMPEG_PATH) if FFMPEG_PATH else None

PATH_ENV = build_path_env(ffmpeg_path=FFMPEG_PATH)
