import logging
import random
import time

from backend.data.app_config import get_bili_cookies
from backend.services.bili_wbi import fetch_wbi_search_page, request_legacy_search

log = logging.getLogger(__name__)

BILI_API_LIMIT = 20
BILI_MAX_PAGES = 10
BILI_PAGE_DELAY = 1.0
BILI_PAGE_RETRY_MAX = 2
BILI_RATE_LIMIT_CODES = {-412, -799, -509}


def _cookie_candidates() -> list[str]:
    cookies = [c.strip() for c in get_bili_cookies() if (c or "").strip()]
    return cookies if cookies else [""]


def _parse_bili_item(item: dict) -> dict:
    title = item.get("title", "")
    title = title.replace('<em class="keyword">', "").replace("</em>", "")
    pic = item.get("pic", "")
    if pic.startswith("//"):
        pic = "https:" + pic
    return {
        "id": item.get("bvid"),
        "title": title,
        "author": item.get("author", ""),
        "link": f"https://www.bilibili.com/video/{item.get('bvid')}",
        "pic": pic,
        "pubdate": item.get("pubdate"),
        "duration": item.get("duration"),
        "view": item.get("play", 0),
        "platform": "bili",
    }


def _is_transient_error(err: str) -> bool:
    text = (err or "").lower()
    return any(
        kw in text
        for kw in (
            "timed out",
            "timeout",
            "handshake",
            "connection reset",
            "connection refused",
            "urlopen error",
            "curl: (28)",
            "curl: (35)",
            "ssl",
        )
    )


def _fetch_search_page(params: dict, cookie: str, *, use_wbi: bool) -> tuple[dict | None, str | None]:
    if use_wbi:
        return fetch_wbi_search_page(params, cookie)
    return request_legacy_search(params, cookie)


def search_bili(keyword: str, order: str = "pubdate", pages: int = 1) -> dict:
    page_count = max(1, min(int(pages), BILI_MAX_PAGES))
    videos: list[dict] = []
    seen: set[str] = set()
    total_reported = 0
    raw_count = 0
    pages_fetched = 0
    warning = ""
    cookies = _cookie_candidates()
    cookie_index = 0

    for page in range(1, page_count + 1):
        if page > 1:
            time.sleep(BILI_PAGE_DELAY + random.uniform(0, 0.4))

        params = {
            "search_type": "video",
            "keyword": keyword,
            "order": order,
            "page": page,
            "pagesize": BILI_API_LIMIT,
            "duration": 0,
            "tids": 0,
        }

        data = None
        last_error = ""
        for attempt in range(BILI_PAGE_RETRY_MAX):
            cookie = cookies[cookie_index % len(cookies)]
            data, last_error = _fetch_search_page(
                params,
                cookie,
                use_wbi=True,
            )
            if data is not None:
                break
            if len(cookies) > 1:
                cookie_index += 1
            if attempt + 1 < BILI_PAGE_RETRY_MAX:
                backoff = min(10.0, (attempt + 1) * 2.5) + random.uniform(0, 1.0)
                log.info(
                    "B站搜索第 %s 页失败，%ss 后重试: %s",
                    page,
                    round(backoff, 1),
                    (last_error or "")[:160],
                )
                time.sleep(backoff)

        if data is None and last_error and _is_transient_error(last_error):
            data, legacy_err = _fetch_search_page(
                params,
                cookies[cookie_index % len(cookies)],
                use_wbi=False,
            )
            if data is None and legacy_err:
                last_error = legacy_err

        if data is None:
            hint = ""
            if _is_transient_error(last_error or ""):
                hint = "（网络连接 B 站超时，请检查网络/代理或稍后重试）"
            if videos:
                warning = (
                    f"翻页在第 {page} 页停止：{last_error}{hint}。"
                    f"已拉取 {pages_fetched} 页共 {len(videos)} 条"
                )
                break
            err = (last_error or "unknown") + hint
            return {"error": err, "videos": []}

        batch = data.get("data", {}).get("result") or []
        total_reported = data.get("data", {}).get("numResults", 0)
        pages_fetched = page
        if not batch:
            if page < page_count:
                warning = f"第 {page} 页无结果，提前结束（请求 {page_count} 页）"
            break

        for item in batch:
            parsed = _parse_bili_item(item)
            vid = parsed.get("id")
            if not vid:
                continue
            raw_count += 1
            if vid in seen:
                continue
            seen.add(vid)
            videos.append(parsed)

        if len(batch) < BILI_API_LIMIT:
            if page < page_count:
                warning = f"第 {page} 页仅返回 {len(batch)} 条，已无更多结果"
            break

    if not warning and pages_fetched < page_count:
        warning = f"仅拉取 {pages_fetched}/{page_count} 页（共 {len(videos)} 条）"

    return {
        "videos": videos,
        "total": total_reported,
        "raw_count": raw_count,
        "duplicate_count": raw_count - len(videos),
        "pages_fetched": pages_fetched,
        "pages_requested": page_count,
        "warning": warning,
    }
