from __future__ import annotations

from backend.data.filter_patterns import get_filter_patterns
from backend.services.search_bili import BILI_API_LIMIT, search_bili
from backend.services.search_douyin import DOUYIN_API_LIMIT, search_douyin
from backend.services.video_filter import compile_filter_patterns, filter_videos

BILI_SEARCH_LIMIT = BILI_API_LIMIT
DOUYIN_SEARCH_LIMIT = DOUYIN_API_LIMIT


def _bili_order(search_sort: str) -> str:
    return "pubdate" if search_sort == "recent" else "totalrank"


def _douyin_sort(search_sort: str) -> int:
    return 2 if search_sort == "recent" else 0


def search_videos(
    platform: str,
    keyword: str,
    bili_pages: int = 1,
    search_sort: str = "recent",
) -> dict:
    keyword = keyword.strip()
    if not keyword:
        return {"videos": [], "error": "关键词不能为空"}

    patterns, pattern_errors = compile_filter_patterns(get_filter_patterns())
    sort = search_sort if search_sort in ("default", "recent") else "recent"
    if platform == "bili":
        pages = max(1, min(int(bili_pages), 10))
        fetch_limit = BILI_SEARCH_LIMIT * pages
        result = search_bili(keyword, order=_bili_order(sort), pages=pages)
    else:
        fetch_limit = DOUYIN_SEARCH_LIMIT
        result = search_douyin(keyword, sort=_douyin_sort(sort), limit=fetch_limit)

    if result.get("error") and not result.get("videos"):
        return {"videos": [], "error": result["error"]}

    videos = result.get("videos", [])
    filtered_count = 0
    if patterns:
        videos, filtered_count = filter_videos(videos, patterns)
    videos = videos[:fetch_limit]

    out = {
        "platform": platform,
        "keyword": keyword,
        "search_sort": sort,
        "videos": videos,
        "total": len(videos),
        "filtered_count": filtered_count,
        "raw_count": result.get("raw_count"),
        "requested_limit": fetch_limit,
        "pages_fetched": result.get("pages_fetched"),
        "pages_requested": result.get("pages_requested"),
        "pattern_errors": pattern_errors,
        "warning": result.get("warning") or result.get("error") or "",
    }
    return out
