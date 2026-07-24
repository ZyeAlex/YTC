"""统一直连 curl：IPv4、无代理、可 kill、带重试。"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from backend.services.proxy_bypass import curl_no_proxy_args, sanitize_env

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _parse_json(text: str) -> tuple[dict | None, str | None]:
    body = (text or "").strip()
    if not body:
        return None, "空响应"
    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        return None, "非 JSON 响应"


def _retryable(err: str) -> bool:
    text = (err or "").lower()
    return any(
        kw in text
        for kw in (
            "timeout", "timed out", "curl: (28)", "curl: (35)", "curl: (56)",
            "connection reset", "connection refused", "ssl", "empty response",
            "operation timed out", "could not resolve", "transfer closed",
        )
    )


def curl_run(
    args: list[str],
    *,
    timeout: int,
    env: dict | None = None,
) -> tuple[int, str, str]:
    cmd = ["curl", "-4", "-sS", *curl_no_proxy_args(), *args]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sanitize_env(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        return -1, "", "curl: 超时"
    return proc.returncode, stdout or "", stderr or ""


def curl_get_json(
    url: str,
    *,
    cookie: str = "",
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    retries: int = 3,
) -> tuple[dict | None, str | None]:
    """GET JSON，失败时递增超时重试。"""
    last_err = ""
    for attempt in range(max(1, retries)):
        attempt_timeout = min(30, timeout + attempt * 5)
        args = [
            "--connect-timeout", str(min(10, attempt_timeout)),
            "--max-time", str(attempt_timeout),
            "-H", f"User-Agent: {_DEFAULT_UA}",
            "-H", "Accept: application/json, text/plain, */*",
        ]
        if headers:
            for key, val in headers.items():
                if key.lower() not in ("user-agent", "accept"):
                    args.extend(["-H", f"{key}: {val}"])
        if cookie:
            args.extend(["-H", f"Cookie: {cookie}"])
        args.append(url)

        code, stdout, stderr = curl_run(args, timeout=attempt_timeout + 5)
        if code == 0:
            data, perr = _parse_json(stdout)
            if data is not None:
                return data, None
            last_err = perr or "非 JSON"
        else:
            last_err = (stderr or stdout or f"exit {code}").strip()[:200]

        if attempt + 1 < retries and _retryable(last_err):
            time.sleep(0.3 * (attempt + 1))
            continue
        break
    return None, last_err


def curl_download_file(
    url: str,
    output_path: str,
    *,
    cookie: str = "",
    referer: str = "",
    timeout: int = 120,
    min_bytes: int = 50_000,
) -> tuple[bool, str]:
    args = [
        "-L",
        "--connect-timeout", str(min(10, timeout // 3 or 10)),
        "--max-time", str(timeout),
        "-H", f"User-Agent: {_DEFAULT_UA}",
    ]
    if referer:
        args.extend(["-H", f"Referer: {referer}"])
    if cookie:
        args.extend(["-H", f"Cookie: {cookie}"])
    args.extend(["-o", output_path, url])

    code, _, stderr = curl_run(args, timeout=timeout + 10)
    if code != 0:
        return False, (stderr or "curl 下载失败").strip()[:200]

    import os

    try:
        size = os.path.getsize(output_path)
    except OSError:
        return False, "下载文件不存在"
    if size < min_bytes:
        return False, "下载失败，文件太小或不存在"
    try:
        with open(output_path, "rb") as f:
            head = f.read(12)
        if len(head) >= 8 and head[4:8] != b"ftyp":
            return False, "下载内容非 MP4 视频"
    except OSError:
        return False, "无法读取下载文件"
    return True, ""


def is_valid_mp4_file(path: str, *, min_bytes: int = 50_000) -> bool:
    import os

    try:
        if not os.path.exists(path) or os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as f:
            head = f.read(12)
        return len(head) >= 8 and head[4:8] == b"ftyp"
    except OSError:
        return False
