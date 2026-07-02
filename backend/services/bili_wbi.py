"""B站 WBI 签名（搜索等 Web 接口必需）。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

import backend.services.proxy_bypass  # noqa: F401 — 确保进程代理已剥离

from backend.services.proxy_bypass import curl_no_proxy_args, sanitize_env

log = logging.getLogger(__name__)

MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
)

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
WBI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
LEGACY_SEARCH_URL = "https://api.bilibili.com/x/web-interface/search/type"

_wbi_cache: dict[str, Any] = {"mixin_key": "", "fetched_at": 0.0}
_WBI_CACHE_TTL = 6 * 3600
_CONNECT_TIMEOUT = 12
_READ_TIMEOUT = 20
_SEARCH_TIMEOUT = _READ_TIMEOUT
_NAV_TIMEOUT = _READ_TIMEOUT
_NAV_RETRY_MAX = 2
_CURL_CLI_MAX_TIME = 12
_CURL_CFFI_IMPERSONATE = "chrome136"


_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}


def _bili_quote(value: Any) -> str:
    text = quote(str(value), safe="~()*!.'")
    return re.sub(r"%[0-9a-f]{2}", lambda m: m.group(0).upper(), text)


def _extract_wbi_key(url: str) -> str:
    name = (url or "").rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def _gen_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    shuffled = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB if i < len(raw))
    return shuffled[:32]


def sign_wbi_query(params: dict[str, Any], mixin_key: str) -> str:
    """生成已签名的 query 字符串（避免 requests/curl 二次编码）。"""
    signed = {k: str(v) for k, v in params.items() if v is not None and str(v) != ""}
    signed["wts"] = str(int(time.time()))
    query = "&".join(f"{k}={_bili_quote(signed[k])}" for k in sorted(signed))
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return f"{query}&w_rid={w_rid}"


def _headers_with_cookie(cookie: str) -> dict[str, str]:
    headers = dict(_DEFAULT_HEADERS)
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _get_http_session():
    try:
        from curl_cffi.requests import Session

        return Session(impersonate=_CURL_CFFI_IMPERSONATE, trust_env=False)
    except ImportError:
        return None


def _timeout_seconds(timeout: int | tuple[int, int]) -> int:
    if isinstance(timeout, tuple):
        return max(timeout)
    return timeout


def _parse_json_response(raw: str, *, transport: str) -> tuple[dict | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, f"{transport}: 空响应"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, f"{transport}: 非 JSON 响应"


def _request_url_urllib(full_url: str, cookie: str, timeout: int | tuple[int, int]) -> tuple[dict | None, str | None]:
    headers = _headers_with_cookie(cookie)
    req = urllib.request.Request(full_url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=_timeout_seconds(timeout)) as resp:
            return _parse_json_response(resp.read().decode("utf-8"), transport="urllib")
    except urllib.error.HTTPError as e:
        return None, f"urllib: HTTP {e.code}"
    except Exception as e:
        return None, f"urllib: {e}"


def _request_url_curl(full_url: str, cookie: str, timeout: int | tuple[int, int]) -> tuple[dict | None, str | None]:
    session = _get_http_session()
    if session is None:
        return None, "curl_cffi 不可用"
    headers = _headers_with_cookie(cookie)
    seconds = _timeout_seconds(timeout)
    try:
        resp = session.get(
            full_url,
            headers=headers,
            timeout=seconds,
            allow_redirects=True,
            proxies={"http": None, "https": None},
        )
        if resp.status_code != 200:
            return None, f"curl_cffi: HTTP {resp.status_code}"
        return _parse_json_response(resp.text, transport="curl_cffi")
    except Exception as e:
        return None, f"curl_cffi: {e}"


def _request_url_curl_cli(full_url: str, cookie: str, timeout: int | tuple[int, int]) -> tuple[dict | None, str | None]:
    seconds = _timeout_seconds(timeout)
    cmd = [
        "curl",
        "-sS",
        "-4",
        *curl_no_proxy_args(),
        "--max-time",
        str(seconds),
        "-H",
        f"User-Agent: {_DEFAULT_HEADERS['User-Agent']}",
        "-H",
        f"Referer: {_DEFAULT_HEADERS['Referer']}",
        "-H",
        f"Origin: {_DEFAULT_HEADERS['Origin']}",
        "-H",
        f"Accept: {_DEFAULT_HEADERS['Accept']}",
    ]
    if cookie:
        cmd.extend(["-H", f"Cookie: {cookie}"])
    cmd.append(full_url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=seconds + 5,
            env=sanitize_env(),
        )
    except subprocess.TimeoutExpired:
        return None, "curl: 超时"
    except Exception as e:
        return None, f"curl: {e}"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        return None, f"curl: {err[:200]}"
    return _parse_json_response(result.stdout, transport="curl")


def _request_json_url_with_headers(
    full_url: str,
    *,
    cookie: str = "",
    timeout: int | tuple[int, int] = _SEARCH_TIMEOUT,
    extra_headers: dict[str, str] | None = None,
) -> tuple[dict | None, str | None]:
    """带自定义头的 JSON 请求（playurl 等需视频页 Referer）。"""
    if not extra_headers:
        return _request_json_url(full_url, cookie=cookie, timeout=timeout)

    seconds = _timeout_seconds(timeout)
    session = _get_http_session()
    if session is not None:
        headers = _headers_with_cookie(cookie)
        headers.update(extra_headers)
        try:
            resp = session.get(
                full_url,
                headers=headers,
                timeout=seconds,
                allow_redirects=True,
                proxies={"http": None, "https": None},
            )
            if resp.status_code == 200:
                data, err = _parse_json_response(resp.text, transport="curl_cffi")
                if data is not None:
                    return data, None
        except Exception:
            pass

    cmd = [
        "curl", "-sS", "-4", *curl_no_proxy_args(), "--max-time", str(seconds),
        "-H", f"User-Agent: {_DEFAULT_HEADERS['User-Agent']}",
        "-H", f"Accept: {_DEFAULT_HEADERS['Accept']}",
    ]
    for key, val in {**_DEFAULT_HEADERS, **(extra_headers or {})}.items():
        if key.lower() in ("user-agent", "accept"):
            continue
        cmd.extend(["-H", f"{key}: {val}"])
    if cookie:
        cmd.extend(["-H", f"Cookie: {cookie}"])
    cmd.append(full_url)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=seconds + 5, env=sanitize_env(),
        )
        if result.returncode == 0:
            return _parse_json_response(result.stdout, transport="curl")
    except Exception:
        pass
    return _request_json_url(full_url, cookie=cookie, timeout=timeout)


def _request_json_url(
    full_url: str,
    *,
    cookie: str = "",
    timeout: int | tuple[int, int] = _SEARCH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    cli_timeout = _timeout_seconds(timeout)
    last_err = ""
    for fn, t in (
        (_request_url_curl, timeout),
        (_request_url_curl_cli, cli_timeout),
        (_request_url_urllib, timeout),
    ):
        data, err = fn(full_url, cookie, t)
        if data is not None:
            return data, None
        last_err = err or last_err
        log.debug("B站请求 %s 失败: %s", fn.__name__, (err or "")[:160])
    return None, last_err


def _extract_wbi_from_nav(data: dict) -> tuple[str, str]:
    wbi_img = (data.get("data") or {}).get("wbi_img") or {}
    img_key = _extract_wbi_key(str(wbi_img.get("img_url") or ""))
    sub_key = _extract_wbi_key(str(wbi_img.get("sub_url") or ""))
    return img_key, sub_key


def get_mixin_key(*, force_refresh: bool = False, cookie: str = "") -> tuple[str, str | None]:
    now = time.time()
    cached = str(_wbi_cache.get("mixin_key") or "")
    if cached and not force_refresh and now - float(_wbi_cache.get("fetched_at") or 0) < _WBI_CACHE_TTL:
        return cached, None

    last_err = ""
    for attempt in range(_NAV_RETRY_MAX):
        data, err = _request_json_url(NAV_URL, cookie=cookie, timeout=_NAV_TIMEOUT)
        if data is not None:
            img_key, sub_key = _extract_wbi_from_nav(data)
            if img_key and sub_key:
                mixin_key = _gen_mixin_key(img_key, sub_key)
                _wbi_cache["mixin_key"] = mixin_key
                _wbi_cache["fetched_at"] = now
                return mixin_key, None
            last_err = "WBI 密钥为空"
        else:
            last_err = err or "获取 WBI 密钥失败"
        if attempt + 1 < _NAV_RETRY_MAX:
            time.sleep((attempt + 1) * 2)

    if cached:
        return cached, last_err
    return "", last_err


def fetch_wbi_search_page(
    params: dict[str, Any],
    cookie: str = "",
    *,
    force_refresh_key: bool = False,
) -> tuple[dict | None, str | None]:
    mixin_key, key_err = get_mixin_key(force_refresh=force_refresh_key, cookie=cookie)
    if not mixin_key:
        return None, key_err or "WBI 密钥不可用"

    query = sign_wbi_query(params, mixin_key)
    url = f"{WBI_SEARCH_URL}?{query}"
    data, err = _request_json_url(url, cookie=cookie, timeout=_SEARCH_TIMEOUT)
    if data is None:
        return None, err

    code = data.get("code")
    if code == 0:
        return data, None

    msg = str(data.get("message") or "unknown")
    payload = str(data.get("data") or "")
    if code in (-412, -799, -509):
        return None, f"限流(code={code}): {msg}"
    if "签名" in msg or "v_voucher" in payload:
        if not force_refresh_key:
            return fetch_wbi_search_page(params, cookie, force_refresh_key=True)
        return None, f"WBI 签名失败: {msg}"
    return None, f"{msg}(code={code})"


def request_legacy_search(
    params: dict[str, Any],
    cookie: str = "",
) -> tuple[dict | None, str | None]:
    parts = [f"{k}={_bili_quote(v)}" for k, v in sorted(params.items())]
    url = f"{LEGACY_SEARCH_URL}?{'&'.join(parts)}"
    data, err = _request_json_url(url, cookie=cookie, timeout=_SEARCH_TIMEOUT)
    if data is None:
        return None, err
    if data.get("code") == 0:
        return data, None
    msg = str(data.get("message") or "unknown")
    return None, f"{msg}(code={data.get('code')})"
