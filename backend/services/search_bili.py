import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from backend.data.app_config import get_bili_cookies
from backend.services.proxy_bypass import is_proxy_error

log = logging.getLogger(__name__)

BILI_API_LIMIT = 20
BILI_MAX_PAGES = 10
BILI_PAGE_DELAY = 1.0
BILI_PAGE_RETRY_MAX = 3
BILI_RATE_LIMIT_CODES = {-412, -799, -509}


def _load_cookie() -> str:
    cookies = get_bili_cookies()
    return cookies[0] if cookies else ""


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


def _fetch_search_page(url: str, cookie: str) -> tuple[dict | None, str | None]:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
    req.add_header("Referer", "https://search.bilibili.com/")
    if cookie:
        req.add_header("Cookie", cookie)

    # 不走系统 HTTP 代理，避免 Cursor/VPN 代理 403
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    try:
        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        err = str(e)
        if is_proxy_error(err):
            return None, "代理拒绝连接 B站 (HTTP 403)，请关闭系统代理"
        return None, err

    code = data.get("code")
    if code != 0:
        msg = str(data.get("message") or "unknown")
        if code in BILI_RATE_LIMIT_CODES:
            return None, f"限流(code={code}): {msg}"
        return None, f"{msg}(code={code})"
    return data, None


def search_bili(keyword: str, order: str = "pubdate", pages: int = 1) -> dict:
    cookie = _load_cookie()
    encoded_kw = urllib.parse.quote(keyword)
    page_count = max(1, min(int(pages), BILI_MAX_PAGES))
    videos: list[dict] = []
    seen: set[str] = set()
    total_reported = 0
    raw_count = 0
    pages_fetched = 0
    warning = ""

    for page in range(1, page_count + 1):
        if page > 1:
            time.sleep(BILI_PAGE_DELAY + random.uniform(0, 0.4))

        url = (
            f"https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={encoded_kw}&order={order}"
            f"&page={page}&pagesize={BILI_API_LIMIT}"
        )

        data = None
        last_error = ""
        for attempt in range(BILI_PAGE_RETRY_MAX):
            data, last_error = _fetch_search_page(url, cookie)
            if data is not None:
                break
            if attempt + 1 < BILI_PAGE_RETRY_MAX:
                backoff = (attempt + 1) * 1.5 + random.uniform(0, 0.5)
                log.info("B站搜索第 %s 页失败，%ss 后重试: %s", page, round(backoff, 1), last_error)
                time.sleep(backoff)

        if data is None:
            if videos:
                warning = (
                    f"翻页在第 {page} 页停止：{last_error}。"
                    f"已拉取 {pages_fetched} 页共 {len(videos)} 条，可能触发了 B 站限流，请稍后重试或减少页数"
                )
                break
            return {"error": last_error or "unknown", "videos": []}

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
