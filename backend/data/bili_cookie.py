"""B站 Cookie：配置里只存原生 HTTP Cookie 头，按需转换为 Netscape（yt-dlp）。"""

from __future__ import annotations

_BILI_DOMAIN = ".bilibili.com"
_NETSCAPE_EXPIRY = "1893456000"


def _is_netscape_cookie(text: str) -> bool:
    return any("\t" in ln and not ln.lstrip().startswith("#") for ln in text.splitlines())


def netscape_to_header(netscape: str) -> str:
    pairs: list[str] = []
    for line in netscape.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name, value = parts[5].strip(), parts[6].strip()
        if name:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def header_to_netscape(cookie_header: str, *, domain: str = _BILI_DOMAIN) -> str:
    header = (cookie_header or "").strip()
    if not header:
        return ""
    lines = [
        "# Netscape HTTP Cookie File",
        "# This file was generated from config.json → bili.cookie",
        "",
    ]
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not name:
            continue
        lines.append(f"{domain}\tTRUE\t/\tFALSE\t{_NETSCAPE_EXPIRY}\t{name}\t{value}")
    if len(lines) <= 3:
        return ""
    return "\n".join(lines) + "\n"


def normalize_bili_cookie_value(raw: str) -> str:
    """统一为 HTTP Cookie 请求头字符串。"""
    text = (raw or "").strip()
    if not text:
        return ""
    if _is_netscape_cookie(text):
        return netscape_to_header(text)
    return text


def normalize_bili_config(bili: dict) -> dict[str, list[str]]:
    """读取配置时合并旧字段，输出 cookies 数组。"""
    cookies: list[str] = []
    if isinstance(bili.get("cookies"), list):
        for raw in bili["cookies"]:
            value = normalize_bili_cookie_value(str(raw))
            if value and value not in cookies:
                cookies.append(value)
    for key in ("cookie", "search_cookie", "download_cookie_netscape"):
        value = normalize_bili_cookie_value(str(bili.get(key, "")).strip())
        if value and value not in cookies:
            cookies.append(value)
    return {"cookies": cookies}
