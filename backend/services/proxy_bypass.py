"""统一绕过系统 HTTP 代理，避免 Cursor/VPN 代理导致 B站/抖音下载 403。"""
from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)

# 常见代理环境变量（含 macOS / 工具链注入）
PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SOCKS_PROXY", "SOCKS5_PROXY", "socks_proxy", "socks5_proxy",
    "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY",
    "GLOBAL_AGENT_HTTP_PROXY", "GLOBAL_AGENT_HTTPS_PROXY",
)

_PROXY_KEY_UPPER = {k.upper() for k in PROXY_ENV_KEYS}


def _is_proxy_env_key(key: str) -> bool:
    upper = key.upper()
    return upper in _PROXY_KEY_UPPER or upper.endswith("_PROXY")


def sanitize_env(base: dict | None = None) -> dict[str, str]:
    """构造子进程环境：清除代理变量，强制 NO_PROXY=*。"""
    env = dict(base if base is not None else os.environ)
    for key in list(env.keys()):
        if _is_proxy_env_key(key):
            env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def direct_connect_env() -> dict[str, str]:
    """子进程直连环境（基于当前 os.environ）。"""
    return sanitize_env(os.environ)


def strip_process_proxy() -> list[str]:
    """从当前进程环境移除代理，防止 urllib/requests 走系统代理。"""
    removed: list[str] = []
    for key in list(os.environ.keys()):
        if _is_proxy_env_key(key):
            val = os.environ.pop(key, "")
            if val:
                removed.append(f"{key}={val}")
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    if removed:
        log.warning("已禁用进程代理（B站/抖音直连）: %s", ", ".join(removed))
    return removed


@contextmanager
def without_process_proxy() -> Iterator[None]:
    saved = {k: os.environ.pop(k, None) for k in list(os.environ.keys()) if _is_proxy_env_key(k)}
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        yield
    finally:
        for key in ("NO_PROXY", "no_proxy"):
            os.environ.pop(key, None)
        for key, val in saved.items():
            if val is not None:
                os.environ[key] = val


def is_proxy_error(msg: str) -> bool:
    """判断错误是否由 HTTP 代理引起。"""
    text = (msg or "").strip()
    if not text:
        return False
    lower = text.lower()
    if any(x in lower for x in (
        "tunnel connection failed",
        "proxyerror",
        "proxy error",
        "代理拒绝",
        "代理错误",
    )):
        return True
    if "403 forbidden" in lower and any(x in lower for x in ("urlopen", "tunnel", "proxy")):
        return True
    if "unable to download webpage" in lower and any(
        x in lower for x in ("tunnel", "proxy", "403", "urlopen error")
    ):
        return True
    if "407" in text and "proxy" in lower:
        return True
    return False


def curl_no_proxy_args() -> list[str]:
    """curl 额外参数：不走代理。"""
    return ["--noproxy", "*"]


def check_bilibili_direct(timeout: float = 8.0) -> tuple[bool, str]:
    """启动自检：直连 B站是否可达。"""
    req = urllib.request.Request(
        "https://www.bilibili.com/",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status < 400:
                return True, ""
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if is_proxy_error(str(e)):
            return False, f"代理阻断 B站 (HTTP {e.code})"
        return False, f"HTTP {e.code}"
    except Exception as e:
        err = str(e)
        if is_proxy_error(err):
            return False, "代理阻断 B站连接"
        return False, err[:200]


# 模块导入时即剥离进程代理（uvicorn worker 启动后生效）
strip_process_proxy()
