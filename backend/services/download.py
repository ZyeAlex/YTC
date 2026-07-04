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
    YT_DLP_PATH,
)
from backend.data.app_config import get_bili_cookies, get_douyin_cookies
from backend.data.bili_cookie import header_to_netscape
from backend.services.download_deadline import DownloadDeadline
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


def _subprocess_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = PATH_ENV + env.get("PATH", "")
    return env


def _download_env() -> dict:
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
        return "视频格式不可用"
    if _is_transient_download_error(text):
        return "连接 B 站超时"
    if not text:
        return "下载失败"
    return text[:400]


def _is_transient_download_error(err: str) -> bool:
    text = (err or "").lower()
    return any(
        kw in text
        for kw in (
            "timed out", "timeout", "handshake", "connection reset",
            "connection refused", "incomplete read", "transport error",
            "unable to download webpage", "urlopen error", "curl: (28)",
            "curl: (35)", "ssl", "read operation timed out", "超时",
            "连接 b 站超时", "operation too slow", "incompleteread",
        )
    )


def _load_douyin_parser(cookie: str = ""):
    spec = importlib.util.spec_from_file_location("douyin_nocookie", DOUYIN_PARSER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DouyinNoCookieParser(cookie=cookie)


def _douyin_cookie_candidates() -> list[str]:
    cookies = get_douyin_cookies()
    return cookies if cookies else [""]


def should_skip_download(err: str) -> bool:
    text = err or ""
    if "超过" in text and "MB 限制" in text:
        return True
    if "图文帖" in text and "无视频" in text:
        return True
    if "HTTP 412" in text or "B站拒绝下载" in text:
        return True
    for kw in (
        "作品不见了", "已被删除", "作品权限", "不可观看", "设为私密",
        "status_self_see", "status_delete",
        "extractor error", "KeyError('bvid')", "KeyError(\"bvid\")",
        "Video unavailable", "Private video", "此视频不存在", "视频不存在",
        "稿件不可见", "版权限制", "地区限制",
    ):
        if kw in text:
            return True
    return False


def _oversize_message(size_mb: float) -> str:
    return f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制"


def _bili_network_args(*, download: bool = False) -> list[str]:
    return [
        "--proxy", "",
        "--socket-timeout", "60" if download else "15",
        "--retries", "3",
        "--extractor-retries", "2",
        "--fragment-retries", "3",
        "--retry-sleep", "linear=1::2",
        "--impersonate", "Chrome-133:Macos-15",
        *(
            ["--downloader-args", "curl:--speed-limit 512 --speed-time 120"]
            if download
            else []
        ),
    ]


def _bili_base_cmd(url: str, cookie: Path | None, *, download: bool = False) -> list[str]:
    cmd = [
        YT_DLP_PATH,
        "--no-update",
        "--no-playlist",
        *_bili_network_args(download=download),
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


def _is_valid_mp4(path: str) -> bool:
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 50000:
            return False
        with open(path, "rb") as f:
            header = f.read(12)
        return len(header) >= 8 and header[4:8] == b"ftyp"
    except Exception:
        return False


def is_valid_local_video(path: str) -> bool:
    return _is_valid_mp4(path)


def _api_error_skip_ytdlp(err: str) -> bool:
    """仅永久错误跳过 yt-dlp 兜底。"""
    text = err or ""
    if should_skip_download(text):
        return True
    for kw in ("412", "权限", "版权", "地区限制", "404"):
        if kw in text:
            return True
    return False


def _download_bili_ytdlp_once(
    bvid: str,
    url: str,
    output_path: str,
    deadline: DownloadDeadline,
) -> tuple[bool, str, bool]:
    if deadline.expired():
        return False, "下载超时", False

    last_err = ""
    had_cookie = False
    fmt_spec = "bestvideo[height<=720]+bestaudio/best[height<=720]"
    ytdlp_timeout = deadline.curl_timeout(DOWNLOAD_TIMEOUT)

    for header in _bili_cookie_candidates():
        cookie = _cookie_file(header)
        had_cookie = had_cookie or cookie is not None
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

        cmd = [
            *_bili_base_cmd(url, cookie, download=True),
            "--no-progress",
            "--max-filesize", f"{MAX_SIZE_MB}M",
            "-f", fmt_spec,
            "--merge-output-format", "mp4",
            "-o", output_path,
        ]
        try:
            result = _run_ytdlp(cmd, ytdlp_timeout)
        except subprocess.TimeoutExpired:
            return False, "下载超时", False
        except Exception as e:
            last_err = str(e)
            continue

        if result.returncode == 0 and os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb > MAX_SIZE_MB:
                os.remove(output_path)
                return False, _oversize_message(size_mb), True
            return True, "", False

        combined = result.stderr + result.stdout
        last_err = _parse_ytdlp_error(result.stderr, result.stdout, has_cookie=cookie is not None)
        if "File is larger than max-filesize" in combined:
            return False, _oversize_message(MAX_SIZE_MB + 1), True
        if should_skip_download(last_err):
            return False, last_err, True
        if is_proxy_error(last_err):
            return False, last_err, False

    if not had_cookie:
        return False, (
            "B站下载需要 Cookie（设置页 → B站 Cookie 未配置）。"
            "请在浏览器登录 bilibili.com 后，从开发者工具复制 Cookie 字符串"
        ), False
    if _is_transient_download_error(last_err):
        return False, f"{last_err}（网络连接 B 站不稳定，请稍后重试）", False
    return False, last_err or "yt-dlp 下载失败", False


def download_bili(
    bvid: str,
    output_path: str,
    *,
    deadline_sec: float | None = None,
) -> tuple[bool, str, bool]:
    deadline = DownloadDeadline(deadline_sec)
    url = f"https://www.bilibili.com/video/{bvid}"
    last_err = ""

    headers = _bili_cookie_candidates()
    header = headers[0] if headers else ""
    api_budget = min(50.0, deadline.remaining() * 0.35)
    if api_budget >= 15:
        from backend.services.bili_api_download import download_bili_via_api

        api_ok, api_err, api_skip = download_bili_via_api(
            bvid, output_path, header, deadline_sec=api_budget,
        )
        if api_ok:
            return True, "", False
        if api_skip:
            return False, api_err, True
        if api_err:
            last_err = api_err
            if _api_error_skip_ytdlp(api_err):
                return False, api_err, True

    remaining = deadline.remaining()
    if remaining < 30:
        return False, last_err or "下载超时", False

    ok, err, skip = _download_bili_ytdlp_once(bvid, url, output_path, deadline)
    if ok:
        return True, "", False
    if skip:
        return False, err, True
    return False, err or last_err or "所有下载策略均失败", False


def download_douyin(
    share_url: str,
    output_path: str,
    *,
    deadline_sec: float | None = None,
) -> tuple[bool, str, bool]:
    if not share_url:
        return False, "缺少视频链接", False

    if is_valid_local_video(output_path):
        return True, "", False

    deadline = DownloadDeadline(deadline_sec)
    saved = {k: os.environ.pop(k, None) for k in _PROXY_ENV_KEYS}
    try:
        output_dir = str(Path(output_path).parent)
        dl_env = _download_env()
        last_err = "解析或下载失败"
        last_skip = False
        for cookie in _douyin_cookie_candidates():
            if deadline.expired():
                return False, "下载超时", False
            parser = _load_douyin_parser(cookie)
            result = parser.download(share_url, output_dir, env=dl_env, deadline=deadline)
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
                    return False, probe.get("reason", "视频不可下载"), True
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
