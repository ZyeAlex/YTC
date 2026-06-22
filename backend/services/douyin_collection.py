"""抖音 Web 收藏列表（listcollection）与账号信息（profile/self）。"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import secrets
import shutil
import string
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

from backend.data.app_config import get_douyin_cookies

log = logging.getLogger(__name__)

WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
_WINDOW_ENV = "1920|1080|1920|1080|0|30|0|0|1920|1080|1920|1080|1525|747|24|24|MacIntel"
_ABOGUS_JS = Path(__file__).resolve().parent.parent / "tools" / "douyin_abogus.js"

_BASE_QUERY = (
    "device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&publish_video_strategy_type=2&pc_client_type=1&update_version_code=170400"
    "&support_h265=1&support_dash=1&version_code=170400&version_name=17.4.0"
    "&cookie_enabled=true&screen_width=1920&screen_height=1080"
    "&browser_language=zh-CN&browser_platform=MacIntel&browser_name=Chrome"
    "&browser_version=132.0.0.0&browser_online=true&engine_name=Blink"
    "&engine_version=132.0.0.0&os_name=Mac+OS&os_version=10.15.7"
    "&cpu_core_num=10&device_memory=8&platform=PC"
)

_LIST_HOSTS = (
    "https://www.douyin.com/aweme/v1/web/aweme/listcollection/",
    "https://www-hj.douyin.com/aweme/v1/web/aweme/listcollection/",
)
_PROFILE_HOSTS = (
    "https://www.douyin.com/aweme/v1/web/user/profile/self/",
    "https://www-hj.douyin.com/aweme/v1/web/user/profile/self/",
)

_FAVORITE_REFERER = "https://www.douyin.com/user/self?showTab=favorite_collection&showSubTab=video"
_SESSION_TTL = 600.0
_session_cache: dict[int, tuple[float, Any, dict[str, str]]] = {}


def _direct_env():
    try:
        from backend.services.proxy_bypass import direct_connect_env

        return direct_connect_env()
    except ImportError:
        return os.environ.copy()


def _cookie_field(cookie: str, name: str) -> str:
    m = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]*)", cookie or "")
    return urllib.parse.unquote(m.group(1)) if m else ""


def _parse_cookie_string(cookie: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _serialize_cookie(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if k and v is not None)


def _merge_cookie_dict(base: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    for k, v in extra.items():
        if v:
            merged[k] = v
    return merged


def _random_ms_token(length: int = 120) -> str:
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _ensure_web_tokens(cookies: dict[str, str]) -> dict[str, str]:
    out = dict(cookies)
    if not out.get("msToken"):
        out["msToken"] = _random_ms_token()
    if not out.get("s_v_web_id"):
        out["s_v_web_id"] = f"verify_{secrets.token_hex(8)}"
    return out


def _webid_from_cookie(cookie: str | dict[str, str]) -> str:
    if isinstance(cookie, dict):
        cookie = _serialize_cookie(cookie)
    for key in ("webid", "device_id"):
        val = _cookie_field(cookie, key)
        if val:
            return val
    cache = _cookie_field(cookie, "PhoneResumeUidCacheV1")
    if cache.startswith("{"):
        try:
            data = json.loads(cache)
            if isinstance(data, dict) and data:
                return next(iter(data.keys()), "")
        except json.JSONDecodeError:
            pass
    return ""


def _build_query(cookie: str | dict[str, str], extra: dict[str, str] | None = None) -> str:
    if isinstance(cookie, dict):
        cookie = _serialize_cookie(cookie)
    parts = [_BASE_QUERY]
    webid = _webid_from_cookie(cookie)
    if webid:
        parts.append(f"webid={urllib.parse.quote(webid, safe='')}")
    uifid = _cookie_field(cookie, "UIFID")
    if uifid:
        parts.append(f"uifid={urllib.parse.quote(uifid, safe='')}")
    fp = _cookie_field(cookie, "s_v_web_id")
    if fp:
        parts.append(f"verifyFp={urllib.parse.quote(fp, safe='')}")
        parts.append(f"fp={urllib.parse.quote(fp, safe='')}")
    ms_token = _cookie_field(cookie, "msToken")
    if ms_token:
        parts.append(f"msToken={urllib.parse.quote(ms_token, safe='')}")
    if extra:
        for k, v in extra.items():
            if v:
                parts.append(f"{k}={urllib.parse.quote(str(v), safe='')}")
    return "&".join(parts)


def _node_bin() -> str:
    for candidate in ("/usr/local/bin/node", shutil.which("node") or ""):
        if candidate and os.path.isfile(candidate):
            return candidate
    return "node"


def _generate_abogus(query: str, body: str = "", *, sign_with_body: bool = True) -> str:
    if not _ABOGUS_JS.is_file():
        return ""
    sign_body = body if sign_with_body else ""
    cmd = [_node_bin(), str(_ABOGUS_JS), query, sign_body, WEB_UA, _WINDOW_ENV]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=8,
            env=_direct_env(),
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("a_bogus 生成失败: %s", e)
        return ""
    token = (result.stdout or "").strip()
    if result.returncode != 0 or not token:
        log.warning("a_bogus 脚本异常: %s", (result.stderr or "").strip()[:200])
        return ""
    return token


def _api_headers(cookie: str | dict[str, str], referer: str, *, post: bool = False) -> dict[str, str]:
    if isinstance(cookie, dict):
        cookie = _serialize_cookie(cookie)
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "cookie": cookie,
        "referer": referer,
        "user-agent": WEB_UA,
        "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    uifid = _cookie_field(cookie, "UIFID")
    if uifid:
        headers["uifid"] = uifid
    bd = _cookie_field(cookie, "bd_ticket_guard_client_data")
    if bd:
        headers["bd-ticket-guard-client-data"] = bd
    if post:
        headers.update(
            {
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": "https://www.douyin.com",
                "x-secsdk-csrf-token": "DOWNGRADE",
            }
        )
    return headers


def _get_http_session():
    try:
        from curl_cffi.requests import Session

        return Session(impersonate="chrome131")
    except ImportError:
        return None


def _warmup_session(http_session, cookies: dict[str, str]) -> dict[str, str]:
    """访问首页与收藏页，合并 Set-Cookie 并补齐 msToken。"""
    merged = _ensure_web_tokens(cookies)
    urls = (
        "https://www.douyin.com/",
        _FAVORITE_REFERER,
    )
    headers = {"user-agent": WEB_UA, "accept": "text/html,application/xhtml+xml"}
    for url in urls:
        try:
            resp = http_session.get(
                url,
                cookies=merged,
                headers=headers,
                timeout=12,
                allow_redirects=True,
            )
            if resp.cookies:
                merged = _merge_cookie_dict(merged, dict(resp.cookies))
        except Exception as e:
            log.debug("预热 %s 失败: %s", url, e)
    return _ensure_web_tokens(merged)


def _get_cached_session(cookie_index: int, *, force_refresh: bool = False) -> tuple[Any, dict[str, str]]:
    now = time.time()
    if not force_refresh and cookie_index in _session_cache:
        ts, http_session, cookies = _session_cache[cookie_index]
        if now - ts < _SESSION_TTL:
            return http_session, cookies

    raw_cookies = [c.strip() for c in get_douyin_cookies() if (c or "").strip()]
    if cookie_index < 0 or cookie_index >= len(raw_cookies):
        raise ValueError(f"无效的抖音账号索引: {cookie_index}")

    http_session = _get_http_session()
    cookies = _ensure_web_tokens(_parse_cookie_string(raw_cookies[cookie_index]))
    if http_session is not None:
        cookies = _warmup_session(http_session, cookies)
    _session_cache[cookie_index] = (now, http_session, cookies)
    return http_session, cookies


def _invalidate_session(cookie_index: int) -> None:
    _session_cache.pop(cookie_index, None)


def _parse_response_text(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {"_error": "空响应", "_raw": ""}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_error": "响应不是 JSON", "_raw": raw[:500]}


def _http_request(
    method: str,
    url: str,
    cookies: dict[str, str],
    *,
    referer: str,
    data: str | None = None,
    http_session=None,
) -> dict[str, Any]:
    cookie_str = _serialize_cookie(cookies)
    headers = _api_headers(cookie_str, referer, post=method.upper() == "POST")

    if http_session is not None:
        try:
            if method.upper() == "POST":
                resp = http_session.post(
                    url,
                    cookies=cookies,
                    headers=headers,
                    data=data or "",
                    timeout=18,
                )
            else:
                resp = http_session.get(url, cookies=cookies, headers=headers, timeout=18)
            return _parse_response_text(resp.text)
        except Exception as e:
            return {"_error": str(e), "_raw": ""}

    env = _direct_env()
    cmd = ["curl", "-sS", url, "--noproxy", "*", "--max-time", "18"]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    if method.upper() == "POST":
        cmd.extend(["-X", "POST", "--data-raw", data or ""])
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0 and not (result.stdout or "").strip():
        return {"_error": (result.stderr or "curl 失败").strip(), "_raw": ""}
    return _parse_response_text(result.stdout)


def _api_error(data: dict[str, Any]) -> str:
    if data.get("_error"):
        err = str(data["_error"])
        raw = (data.get("_raw") or "").strip()
        if err == "响应不是 JSON" and raw:
            if raw.lower().startswith("<!doctype") or raw.lower().startswith("<html"):
                return "接口返回了网页而非 JSON（签名失效、限流或 Cookie 过期）"
            if len(raw) < 80:
                return f"接口异常响应: {raw}"
        return err
    code = data.get("status_code")
    if code not in (0, None):
        msg = str(data.get("status_msg") or f"接口错误 status_code={code}")
        if code == 8 or "bogus" in msg.lower():
            return f"签名无效: {msg}"
        return msg
    return ""


def _listcollection_request(
    cookie_index: int,
    cursor: int,
    count: int,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], str]:
    """请求 listcollection，返回 (data, error)。"""
    try:
        http_session, cookies = _get_cached_session(cookie_index, force_refresh=force_refresh)
    except ValueError as e:
        return {}, str(e)

    body = f"count={count}&cursor={cursor}"
    query = _build_query(cookies)
    last_err = ""
    data: dict[str, Any] = {}

    sign_modes = (True, False)  # POST: 先 query+body，再仅 query
    for host in _LIST_HOSTS:
        for sign_with_body in sign_modes:
            abogus = _generate_abogus(query, body, sign_with_body=sign_with_body)
            signed_query = query
            if abogus:
                signed_query = f"{query}&a_bogus={urllib.parse.quote(abogus, safe='')}"
            url = f"{host}?{signed_query}"

            for attempt in range(3):
                data = _http_request(
                    "POST",
                    url,
                    cookies,
                    referer=_FAVORITE_REFERER,
                    data=body,
                    http_session=http_session,
                )
                last_err = _api_error(data)
                if not last_err:
                    return data, ""
                retryable = any(
                    x in last_err
                    for x in ("不是 JSON", "空响应", "网页而非 JSON", "签名", "限流")
                )
                if retryable and attempt < 2:
                    time.sleep(1.0 * (attempt + 1) + random.uniform(0.1, 0.4))
                    continue
                break
            if not last_err:
                break
        if not last_err:
            break

    return data, last_err


def _aweme_to_video(aweme: dict[str, Any]) -> dict[str, Any] | None:
    aweme_id = str(aweme.get("aweme_id") or "")
    if not aweme_id:
        return None
    author = aweme.get("author") or {}
    nickname = author.get("nickname", "") if isinstance(author, dict) else ""
    desc = (aweme.get("desc") or nickname or aweme_id).strip()
    cover = ""
    video = aweme.get("video") or {}
    if isinstance(video, dict):
        cover_obj = video.get("cover") or video.get("origin_cover") or {}
        if isinstance(cover_obj, dict):
            urls = cover_obj.get("url_list") or []
            cover = urls[0] if urls else ""
    play_addr = ""
    if isinstance(video, dict):
        play = video.get("play_addr") or {}
        if isinstance(play, dict):
            urls = play.get("url_list") or []
            play_addr = urls[0] if urls else ""
    return {
        "id": aweme_id,
        "title": desc[:200],
        "author": nickname,
        "link": f"https://www.douyin.com/video/{aweme_id}",
        "play_addr": play_addr,
        "pic": cover,
        "platform": "douyin",
    }


def get_douyin_cookie_accounts() -> list[dict[str, Any]]:
    """返回配置中每个抖音 cookie 对应的账号摘要。"""
    cookies = get_douyin_cookies()
    accounts: list[dict[str, Any]] = []
    referer = "https://www.douyin.com/user/self?showTab=favorite_collection"
    for idx, cookie in enumerate(cookies):
        cookie = (cookie or "").strip()
        if not cookie:
            continue
        label = f"账号 {idx + 1}"
        uid = ""
        avatar = ""
        nickname = ""
        err = ""
        try:
            http_sess, jar = _get_cached_session(idx)
            cookie_str = _serialize_cookie(jar)
        except ValueError:
            http_sess = None
            cookie_str = cookie
        query = _build_query(cookie_str)
        data: dict[str, Any] = {}
        for host in _PROFILE_HOSTS:
            abogus = _generate_abogus(query)
            url = f"{host}?{query}"
            if abogus:
                url = f"{url}&a_bogus={urllib.parse.quote(abogus, safe='')}"
            data = _http_request(
                "GET",
                url,
                _parse_cookie_string(cookie_str),
                referer=referer,
                http_session=http_sess,
            )
            err = _api_error(data)
            if not err and data.get("user"):
                break
        user = data.get("user") or {}
        if isinstance(user, dict) and user:
            nickname = str(user.get("nickname") or "").strip()
            uid = str(user.get("uid") or user.get("sec_uid") or "")
            avatars = (user.get("avatar_thumb") or {}).get("url_list") or []
            avatar = avatars[0] if avatars else ""
            if nickname:
                label = nickname
        accounts.append(
            {
                "index": idx,
                "label": label,
                "nickname": nickname,
                "uid": uid,
                "avatar": avatar,
                "error": err,
            }
        )
    return accounts


def fetch_douyin_collection(
    cookie_index: int = 0,
    cursor: int = 0,
    count: int = 20,
) -> dict[str, Any]:
    """拉取指定 cookie 账号的收藏视频列表。"""
    cookies = [c.strip() for c in get_douyin_cookies() if (c or "").strip()]
    if not cookies:
        return {"videos": [], "error": "未配置抖音 Cookie，请先在设置页添加"}
    if cookie_index < 0 or cookie_index >= len(cookies):
        return {"videos": [], "error": f"无效的抖音账号索引: {cookie_index}"}

    count = max(1, min(int(count), 20))
    cursor = max(0, int(cursor))

    data, last_err = _listcollection_request(cookie_index, cursor, count)
    if last_err:
        _invalidate_session(cookie_index)
        data, last_err = _listcollection_request(cookie_index, cursor, count, force_refresh=True)
    if last_err:
        hint = "请确认 Cookie 已登录且未过期；若仍失败请重新从浏览器复制完整 Cookie"
        return {"videos": [], "error": f"{last_err}（{hint}）", "cursor": cursor, "has_more": False}

    awemes = data.get("aweme_list") or []
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in awemes:
        if not isinstance(item, dict):
            continue
        video = _aweme_to_video(item)
        if video and video["id"] not in seen:
            seen.add(video["id"])
            videos.append(video)

    next_cursor = data.get("cursor", cursor)
    has_more = bool(data.get("has_more"))
    return {
        "videos": videos,
        "cursor": next_cursor,
        "has_more": has_more,
        "count": len(videos),
        "cookie_index": cookie_index,
    }


def fetch_douyin_collection_all(
    cookie_index: int = 0,
    *,
    max_items: int = 2000,
    page_size: int = 20,
    page_delay: float = 1.0,
) -> dict[str, Any]:
    """翻页拉取全部收藏，直至 has_more 为 false 或达到 max_items。"""
    max_items = max(1, min(int(max_items), 5000))
    page_size = max(1, min(int(page_size), 20))
    all_videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = 0
    pages = 0
    has_more = False
    error = ""

    while len(all_videos) < max_items:
        count = min(page_size, max_items - len(all_videos))
        page = fetch_douyin_collection(cookie_index, cursor, count)
        pages += 1
        if page.get("error"):
            error = str(page["error"])
            if not all_videos and pages == 1:
                time.sleep(1.5)
                page = fetch_douyin_collection(cookie_index, cursor, count)
                pages += 1
                if not page.get("error"):
                    error = ""
                else:
                    error = str(page["error"])
            if page.get("error"):
                if not all_videos:
                    return {
                        "videos": [],
                        "error": error,
                        "cursor": cursor,
                        "has_more": False,
                        "count": 0,
                        "truncated": False,
                        "pages_fetched": pages,
                        "cookie_index": cookie_index,
                    }
                break

        for item in page.get("videos") or []:
            vid = item.get("id")
            if vid and vid not in seen:
                seen.add(vid)
                all_videos.append(item)

        has_more = bool(page.get("has_more"))
        if not has_more:
            break
        cursor = int(page.get("cursor", cursor))
        if pages >= 200:
            has_more = True
            break
        delay = page_delay + random.uniform(0.15, 0.45)
        if delay > 0:
            time.sleep(delay)

    truncated = has_more or len(all_videos) >= max_items
    return {
        "videos": all_videos,
        "cursor": cursor,
        "has_more": has_more,
        "count": len(all_videos),
        "truncated": truncated,
        "pages_fetched": pages,
        "cookie_index": cookie_index,
        "error": error,
    }


COLLECTION_SYNC_TAIL_CHECK = 3
COLLECTION_SYNC_HEAD_BASELINE = 20


def fetch_douyin_collection_diff(
    cookie_index: int,
    existing_ids: set[str],
    *,
    existing_newest_ids: list[str] | None = None,
    max_new: int = 500,
    page_size: int = 20,
    page_delay: float = 1.2,
) -> dict[str, Any]:
    """从收藏第一页（最新）开始增量同步。

    每拉取一页后，取该页最后 3 条与任务列表**头部**最新 20 条比对；
    只要有一条重复，说明已追上上次同步位置，停止翻页。
    """
    max_new = max(1, min(int(max_new), 500))
    page_size = max(1, min(int(page_size), 20))
    known = set(existing_ids)
    orig_existing = set(existing_ids)
    baseline = set((existing_newest_ids or [])[:COLLECTION_SYNC_HEAD_BASELINE])
    new_videos: list[dict[str, Any]] = []
    cursor = 0
    pages = 0
    error = ""
    stopped_on_duplicate = False

    while len(new_videos) < max_new:
        page = fetch_douyin_collection(cookie_index, cursor, page_size)
        pages += 1
        if page.get("error"):
            error = str(page["error"])
            if not new_videos and pages == 1:
                time.sleep(1.5)
                page = fetch_douyin_collection(cookie_index, cursor, page_size)
                pages += 1
                if not page.get("error"):
                    error = ""
                else:
                    error = str(page["error"])
                if page.get("error") and not new_videos:
                    return {
                        "videos": [],
                        "added": 0,
                        "error": error,
                        "pages_fetched": pages,
                        "stopped_on_duplicate": False,
                    }
            if page.get("error"):
                break

        items = page.get("videos") or []
        if not items:
            break

        page_ids = [v.get("id") for v in items if v.get("id")]
        if page_ids and all(pid in orig_existing for pid in page_ids):
            stopped_on_duplicate = True
            break

        for v in items:
            vid = v.get("id")
            if not vid or vid in known:
                continue
            new_videos.append(v)
            known.add(vid)

        if baseline:
            tail = (
                items[-COLLECTION_SYNC_TAIL_CHECK:]
                if len(items) >= COLLECTION_SYNC_TAIL_CHECK
                else items
            )
            if any((v.get("id") in baseline for v in tail if v.get("id"))):
                stopped_on_duplicate = True
                break

        if not page.get("has_more"):
            break
        cursor = int(page.get("cursor", cursor))
        delay = page_delay + random.uniform(0.15, 0.45)
        if delay > 0:
            time.sleep(delay)

    return {
        "videos": new_videos,
        "added": len(new_videos),
        "error": error,
        "pages_fetched": pages,
        "stopped_on_duplicate": stopped_on_duplicate,
    }


def fetch_douyin_collection_new(
    cookie_index: int,
    existing_ids: set[str],
    *,
    existing_newest_ids: list[str] | None = None,
    max_new: int = 500,
    page_size: int = 20,
    page_delay: float = 1.2,
) -> dict[str, Any]:
    """增量同步收藏（手动同步 / 长期任务均使用同一逻辑）。"""
    return fetch_douyin_collection_diff(
        cookie_index,
        existing_ids,
        existing_newest_ids=existing_newest_ids,
        max_new=max_new,
        page_size=page_size,
        page_delay=page_delay,
    )
