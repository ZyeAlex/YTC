"""从粘贴文本中提取抖音分享链接并解析为视频列表。"""
from __future__ import annotations

import importlib.util
import re
from typing import Any

from backend.config import DOUYIN_PARSER
from backend.data.app_config import get_douyin_cookies

DOUYIN_URL_RE = re.compile(
    r"https?://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com)/[^\s\]\>\"']+",
    re.IGNORECASE,
)


def extract_douyin_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for raw in DOUYIN_URL_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?)\"'】、，。")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _load_parser_class():
    spec = importlib.util.spec_from_file_location("douyin_nocookie", DOUYIN_PARSER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DouyinNoCookieParser


def _douyin_cookie_candidates() -> list[str]:
    cookies = get_douyin_cookies()
    return cookies if cookies else [""]


def _parse_video(url: str) -> dict[str, Any] | None:
    parser_cls = _load_parser_class()
    last_info: dict[str, Any] | None = None
    for cookie in _douyin_cookie_candidates():
        info = parser_cls(cookie=cookie).parse_video(url)
        if not info:
            continue
        last_info = info
        if info.get("skip"):
            continue
        return info
    return last_info


def _video_from_info(url: str, info: dict) -> dict[str, Any]:
    video_id = info.get("video_id", "")
    if "v.douyin.com" in url or "/share/" not in url:
        link = url if url.startswith("http") else f"https://{url}"
    else:
        link = f"https://www.iesdouyin.com/share/video/{video_id}/"
    title = (info.get("desc") or info.get("author") or video_id or "抖音视频").strip()
    return {
        "id": video_id,
        "title": title[:200],
        "author": info.get("author", ""),
        "link": link,
        "play_addr": info.get("nwm_url", ""),
        "pic": info.get("cover_url", ""),
        "platform": "douyin",
    }


def parse_douyin_link_list(text: str) -> dict[str, Any]:
    """解析粘贴文本，返回 {videos, errors, url_count}。"""
    urls = extract_douyin_urls(text)
    if not urls:
        return {"videos": [], "errors": [{"url": "", "error": "未找到抖音链接"}], "url_count": 0}

    videos: list[dict] = []
    errors: list[dict] = []
    seen_ids: set[str] = set()

    for url in urls:
        try:
            info = _parse_video(url)
        except Exception as e:
            errors.append({"url": url, "error": str(e)[:120]})
            continue
        if not info:
            errors.append({"url": url, "error": "解析失败"})
            continue
        if info.get("skip"):
            errors.append({"url": url, "error": info.get("reason", "视频不可下载")})
            continue
        video = _video_from_info(url, info)
        if video["id"] in seen_ids:
            continue
        seen_ids.add(video["id"])
        videos.append(video)

    return {"videos": videos, "errors": errors, "url_count": len(urls)}
