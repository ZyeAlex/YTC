from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

from backend.config import (
    CACHE_DIR,
    DOUYIN_PARSER,
    DOWNLOAD_DIR,
    DOWNLOAD_TIMEOUT,
    FFMPEG_PATH,
    MAX_SIZE_MB,
    PATH_ENV,
    PROBE_TIMEOUT,
    YT_DLP_PATH,
)
from backend.data.app_config import get_bili_cookies, get_douyin_cookies
from backend.data.bili_cookie import header_to_netscape


def _subprocess_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = PATH_ENV + env.get("PATH", "")
    return env


from backend.services.proxy_bypass import (
    curl_no_proxy_args,
    is_proxy_error,
    sanitize_env,
)

_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
    "SOCKS_PROXY", "SOCKS5_PROXY", "socks_proxy", "socks5_proxy",
)


def _download_env() -> dict:
    """下载直连 B站/抖音，不走系统代理。"""
    return sanitize_env(_subprocess_env())


def _cookie_file(header: str = "") -> Path | None:
    if not header:
        cookies = get_bili_cookies()
        header = cookies[0] if cookies else ""
    if not header or len(header) < 20:
        return None
    content = header_to_netscape(header)
    if not content:
        return None
    import hashlib
    key = hashlib.md5(header.encode()).hexdigest()[:12]
    cache = CACHE_DIR / f"bili_cookie_{key}.txt"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists() or cache.read_text(encoding="utf-8") != content:
        cache.write_text(content, encoding="utf-8")
    return cache


def _bili_cookie_candidates() -> list[str]:
    cookies = get_bili_cookies()
    return cookies if cookies else [""]


def _parse_ytdlp_error(stderr: str, stdout: str, *, has_cookie: bool) -> str:
    text = (stderr + stdout).strip()
    # 去掉版本警告，保留真实错误
    lines = [
        ln for ln in text.splitlines()
        if "older than 90 days" not in ln and "NotOpenSSLWarning" not in ln
    ]
    text = "\n".join(lines).strip()
    if "412" in text or "Precondition Failed" in text:
        return "B站拒绝下载 (HTTP 412)，该视频不可下载"
    if "403 Forbidden" in text and is_proxy_error(text):
        return "代理拒绝连接 B站 (HTTP 403)，请关闭 VPN/系统代理后重试"
    if "Requested format is not available" in text:
        return "视频格式不可用，将尝试其他清晰度"
    if not text:
        return "下载失败"
    return text[:400]


def _load_douyin_parser(cookie: str = ""):
    spec = importlib.util.spec_from_file_location("douyin_nocookie", DOUYIN_PARSER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DouyinNoCookieParser(cookie=cookie)


def _douyin_cookie_candidates() -> list[str]:
    cookies = get_douyin_cookies()
    return cookies if cookies else [""]


def should_skip_download(err: str) -> bool:
    """永久跳过：超大文件、图文帖、已删除/私密、B站412等，重试无意义。"""
    text = err or ""
    if "超过" in text and "MB 限制" in text:
        return True
    if "图文帖" in text and "无视频" in text:
        return True
    if "HTTP 412" in text or "B站拒绝下载" in text:
        return True
    for kw in ("作品不见了", "已被删除", "作品权限", "不可观看", "设为私密", "status_self_see", "status_delete"):
        if kw in text:
            return True
    return False


def _oversize_message(size_mb: float) -> str:
    return f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制"


def _fmt_bytes(fmt: dict) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    return int(size) if size else None


def _bili_base_cmd(url: str, cookie: Path | None) -> list[str]:
    cmd = [
        YT_DLP_PATH,
        "--no-update",
        "--no-playlist",
        "--proxy", "",
        "--socket-timeout", "30",
        url,
    ]
    if FFMPEG_PATH:
        cmd.extend(["--ffmpeg-location", FFMPEG_PATH])
    if cookie:
        cmd.extend([
            "--cookies", str(cookie),
            "--add-header", "Referer:https://www.bilibili.com/",
            "--add-header",
            "User-Agent:Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        ])
    return cmd


def _run_ytdlp(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """运行 yt-dlp，超时后强制结束进程。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_download_env(),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _probe_bili_info(url: str, cookie: Path | None) -> tuple[dict | None, str]:
    cmd = [*_bili_base_cmd(url, cookie), "-J", "--no-download"]
    try:
        result = _run_ytdlp(cmd, PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "获取视频信息超时"
    except Exception as e:
        return None, str(e)
    if result.returncode != 0:
        return None, _parse_ytdlp_error(result.stderr, result.stdout, has_cookie=cookie is not None)
    try:
        return json.loads(result.stdout), ""
    except json.JSONDecodeError:
        return None, "解析视频信息失败"


def _pick_bili_format(info: dict) -> tuple[str | None, str, bool]:
    """根据元数据选清晰度；全部超大则下载前直接跳过。"""
    max_bytes = MAX_SIZE_MB * 1024 * 1024
    fmts = info.get("formats") or []
    videos = [f for f in fmts if f.get("vcodec") not in (None, "none")]
    audios = [
        f for f in fmts
        if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")
    ]

    if not videos:
        return f"best[filesize<{MAX_SIZE_MB}M]/best", "", False

    audio = min(audios, key=lambda f: _fmt_bytes(f) or 0) if audios else None
    audio_bytes = _fmt_bytes(audio) or 3 * 1024 * 1024

    videos.sort(
        key=lambda f: (f.get("height") or 0, -(_fmt_bytes(f) or 0)),
        reverse=True,
    )

    sized: list[tuple[dict, int]] = []
    unknown: list[dict] = []
    for v in videos:
        vb = _fmt_bytes(v)
        if vb is None:
            unknown.append(v)
            continue
        sized.append((v, vb + audio_bytes))

    for v, total in sized:
        if total <= max_bytes:
            if audio:
                return f"{v['format_id']}+{audio['format_id']}", "", False
            return str(v["format_id"]), "", False

    if unknown:
        max_h = max(v.get("height") or 720 for v in unknown)
        return (
            f"bestvideo[height<={max_h}][filesize<{MAX_SIZE_MB}M]+"
            f"bestaudio[filesize<{MAX_SIZE_MB}M]/best[filesize<{MAX_SIZE_MB}M]",
            "",
            False,
        )

    if sized:
        _, smallest = min(sized, key=lambda x: x[1])
        return None, _oversize_message(smallest / (1024 * 1024)), True

    return f"best[filesize<{MAX_SIZE_MB}M]/best", "", False


def _head_content_length(url: str, headers: list[str] | None = None) -> int | None:
    cmd = ["curl", "-sI", "-L", "--max-time", "20", *curl_no_proxy_args()]
    if headers:
        cmd.extend(headers)
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25, env=_download_env())
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.lower().startswith("content-length:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _check_remote_size(url: str, headers: list[str] | None = None) -> tuple[bool, str, bool]:
    """返回 (ok, err, skip)。已知远程体积超大则下载前跳过。"""
    length = _head_content_length(url, headers)
    if length is None:
        return True, "", False
    size_mb = length / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        return False, _oversize_message(size_mb), True
    return True, "", False


def download_bili(bvid: str, output_path: str) -> tuple[bool, str, bool]:
    url = f"https://www.bilibili.com/video/{bvid}"
    last_err = ""
    last_skip = False
    for attempt in range(2):
        ok, err, skip = _download_bili_once(bvid, url, output_path)
        if ok:
            return True, "", False
        last_err = err
        last_skip = skip
        if not is_proxy_error(err) or attempt > 0:
            break
    return False, last_err, last_skip


def _download_bili_once(bvid: str, url: str, output_path: str) -> tuple[bool, str, bool]:
    last_err = ""
    last_skip = False
    had_cookie = False

    for header in _bili_cookie_candidates():
        cookie = _cookie_file(header)
        has_cookie = cookie is not None
        had_cookie = had_cookie or has_cookie

        info, probe_err = _probe_bili_info(url, cookie)
        if probe_err and should_skip_download(probe_err):
            return False, probe_err, True
        if info is not None:
            _, pre_err, pre_skip = _pick_bili_format(info)
            if pre_skip:
                return False, pre_err, True

        format_options = [
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "bestvideo[height<=480]+bestaudio/best[height<=480]",
            f"best[filesize<{MAX_SIZE_MB}M]/best",
        ]
        oversize_err = ""

        for fmt_spec in format_options:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass

            cmd = [
                *_bili_base_cmd(url, cookie),
                "--no-progress",
                "--max-filesize", f"{MAX_SIZE_MB}M",
                "-f", fmt_spec,
                "--merge-output-format", "mp4",
                "-o", output_path,
            ]

            try:
                result = _run_ytdlp(cmd, DOWNLOAD_TIMEOUT)
            except subprocess.TimeoutExpired:
                last_err = "下载超时"
                break
            except Exception as e:
                last_err = str(e)
                break

            if result.returncode == 0 and os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                if size_mb > MAX_SIZE_MB:
                    os.remove(output_path)
                    oversize_err = _oversize_message(size_mb)
                    continue
                return True, "", False

            combined = result.stderr + result.stdout
            last_err = _parse_ytdlp_error(result.stderr, result.stdout, has_cookie=has_cookie)
            if "File is larger than max-filesize" in combined:
                oversize_err = _oversize_message(MAX_SIZE_MB + 1)
                continue
            if "Requested format is not available" in combined:
                continue
            if "格式不可用" in last_err:
                continue
            if should_skip_download(last_err):
                last_skip = True
                break
            if "需要配置 Cookie" in last_err:
                break
            if is_proxy_error(last_err):
                return False, last_err, False

        if oversize_err:
            return False, oversize_err, True
        if last_skip:
            return False, last_err, True

    if not had_cookie:
        return False, (
            "B站下载需要 Cookie（设置页 → B站 Cookie 未配置）。"
            "请在浏览器登录 bilibili.com 后，从开发者工具复制 Cookie 字符串"
        ), False
    if should_skip_download(last_err):
        return False, last_err, True
    return False, last_err or "所有下载策略均失败", False


def _is_valid_mp4(path: str) -> bool:
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 50000:
            return False
        with open(path, "rb") as f:
            header = f.read(12)
        return len(header) >= 8 and header[4:8] == b"ftyp"
    except Exception:
        return False


def download_douyin(share_url: str, output_path: str, play_addr: str = "") -> tuple[bool, str, bool]:
    """返回 (success, error_msg, skip) skip=True 表示图文帖"""
    if not share_url:
        return False, "缺少视频链接", False

    saved = {k: os.environ.pop(k, None) for k in _PROXY_ENV_KEYS}
    try:
        # 搜索返回的 play_addr 会过期，仅作快速尝试，失败则用解析器重拉
        if play_addr:
            base_headers = [
                "-H", "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "-H", "referer: https://www.iesdouyin.com/",
            ]
            for cookie in _douyin_cookie_candidates():
                headers = list(base_headers)
                if cookie:
                    headers.extend(["-H", f"Cookie: {cookie}"])
                ok, err, skip = _check_remote_size(play_addr, headers)
                if not ok:
                    if skip:
                        return False, err, skip
                    continue
                cmd = ["curl", "-L", "-s", "-o", output_path, "--max-time", "120", *curl_no_proxy_args(), *headers, play_addr]
                try:
                    result = subprocess.run(cmd, capture_output=True, timeout=130, env=_download_env())
                except Exception:
                    result = None
                if result and result.returncode == 0 and _is_valid_mp4(output_path):
                    size_mb = os.path.getsize(output_path) / (1024 * 1024)
                    if size_mb > MAX_SIZE_MB:
                        os.remove(output_path)
                        return False, _oversize_message(size_mb), True
                    return True, "", False
                if os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass

        output_dir = str(Path(output_path).parent)
        dl_env = _download_env()
        last_err = "解析或下载失败"
        last_skip = False
        for cookie in _douyin_cookie_candidates():
            parser = _load_douyin_parser(cookie)
            result = parser.download(share_url, output_dir, env=dl_env)
            if result.get("skip"):
                last_err = result.get("error", "视频不可下载")
                last_skip = True
                continue
            if "path" in result and os.path.exists(result["path"]):
                if result["path"] != output_path:
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    os.rename(result["path"], output_path)
                if not _is_valid_mp4(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
                    last_err = "下载的文件不是有效视频"
                    continue
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                if size_mb > MAX_SIZE_MB:
                    os.remove(output_path)
                    return False, _oversize_message(size_mb), True
                return True, "", False
            err = result.get("error", "解析或下载失败")
            last_err = err
            last_skip = should_skip_download(err)
            if last_skip:
                continue
            if err in ("解析失败，无法获取页面数据", "解析失败，无法获取视频 URL", "解析或下载失败"):
                vid = parser.get_video_id(share_url)
                if vid:
                    api_reason = parser._probe_web_detail(vid)
                    if api_reason and should_skip_download(api_reason):
                        return False, api_reason, True
                probe = parser.parse_video(share_url)
                if probe and probe.get("skip"):
                    reason = probe.get("reason", "视频不可下载")
                    return False, reason, True
        if last_skip:
            return False, last_err, True
        return False, last_err, False
    except Exception as e:
        return False, str(e), False
    finally:
        for key, val in saved.items():
            if val is not None:
                os.environ[key] = val


def prepare_output_path(platform: str, video_id: str, title: str) -> str:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^\w\s\-]", "", title)[:40]
    return str(DOWNLOAD_DIR / f"{platform}_{safe_title}_{video_id}.mp4")
