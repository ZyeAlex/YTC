from __future__ import annotations

import json
import random
import time
from datetime import datetime
from typing import Any

from backend.data.accounts import get_token, list_accounts_public
from backend.services.cli_env import run_cli

RATE_LIMIT_MARKERS = ('"code":20063', '"code":20065', '"code":20066')
OIDB_LIMIT_MARKERS = ('"code":153',)

_own_poster_cache: dict[str, tuple[float, set[str]]] = {}
_CACHE_TTL = 3600

# 频道里常见的通用展示名，不能用于判断「本系统账号」
_GENERIC_POSTER_NICKS = frozenset({
    "频道用户", "频道成员", "匿名用户", "。", ".", "…",
})


def _is_numeric_user_id(val: Any) -> bool:
    if val is None or val == "":
        return False
    text = str(val).strip()
    return text.isdigit() and len(text) >= 6


def _collect_numeric_user_ids(data: Any) -> set[str]:
    """仅从用户信息 JSON 提取数字 tinyid/uin，忽略昵称字段。"""
    ids: set[str] = set()
    if isinstance(data, dict):
        for key in (
            "uin", "tiny_id", "tinyId", "tinyid", "author_id", "poster_id", "create_uin", "id",
        ):
            val = data.get(key)
            if _is_numeric_user_id(val):
                ids.add(str(val).strip())
        for nested_key in ("data", "user", "user_info"):
            nested = data.get(nested_key)
            if nested:
                ids |= _collect_numeric_user_ids(nested)
    return ids


def list_all_account_ids() -> list[str]:
    return [a["id"] for a in list_accounts_public()]


def _parse_json_output(output: str) -> Any:
    output = output.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        start = output.find("{")
        if start >= 0:
            try:
                return json.loads(output[start:])
            except json.JSONDecodeError:
                pass
    return None


def _parse_feed_time(feed: dict) -> float:
    for key in ("create_time", "createTime", "created_at", "publish_time"):
        val = feed.get(key)
        if val is None or val == "":
            continue
        if isinstance(val, (int, float)):
            ts = float(val)
            return ts / 1000 if ts > 1e12 else ts
        if isinstance(val, str):
            text = val.strip()[:19]
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    return datetime.strptime(text, fmt).timestamp()
                except ValueError:
                    continue
    return 0.0


def _extract_poster_ids(feed: dict) -> set[str]:
    """提取帖子发布者的数字 ID（tinyid/uin），不用昵称比对。"""
    ids: set[str] = set()
    author = feed.get("author")
    if isinstance(author, dict):
        for key in ("id", "uin", "tiny_id", "tinyId"):
            val = author.get(key)
            if _is_numeric_user_id(val):
                ids.add(str(val).strip())
    elif isinstance(author, str) and _is_numeric_user_id(author):
        ids.add(author.strip())
    poster = feed.get("poster") or feed.get("user") or {}
    if isinstance(poster, dict):
        for key in ("id", "uin", "tiny_id", "tinyId"):
            val = poster.get(key)
            if _is_numeric_user_id(val):
                ids.add(str(val).strip())
    for key in ("author_id", "create_uin", "poster_id", "tiny_id", "uin"):
        val = feed.get(key)
        if _is_numeric_user_id(val):
            ids.add(str(val).strip())
    return ids


def _nicknames_from_user_info(data: Any) -> set[str]:
    nicks: set[str] = set()
    if not isinstance(data, dict):
        return nicks
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    if isinstance(inner, dict):
        for key in ("global_nickname", "member_name", "nickname", "nick", "name"):
            val = inner.get(key)
            if isinstance(val, str) and val.strip():
                nicks.add(val.strip())
    return nicks


def _search_member_tinyids(token: str, guild_id: str, nickname: str) -> set[str]:
    ids: set[str] = set()
    keyword = nickname.strip()
    if not keyword or not guild_id:
        return ids
    try:
        _, output = run_cli(token, [
            "manage", "guild-member-search",
            "--guild-id", guild_id,
            "--keyword", keyword,
            "--json",
        ], timeout=15)
        parsed = _parse_json_output(output)
        if not isinstance(parsed, dict):
            return ids
        members = (parsed.get("data") or {}).get("members") or []
        for member in members:
            if not isinstance(member, dict):
                continue
            if (member.get("nickname") or "").strip() != keyword:
                continue
            for key in ("tinyid", "tiny_id", "uin", "id"):
                val = member.get(key)
                if val not in (None, ""):
                    ids.add(str(val))
    except Exception:
        pass
    return ids


def _collect_user_ids(data: Any) -> set[str]:
    return _collect_numeric_user_ids(data)


def resolve_own_poster_ids(account_ids: list[str], guild_id: str = "") -> set[str]:
    cache_key = f"v2:{guild_id}:{','.join(sorted(account_ids))}"
    cached = _own_poster_cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]

    ids: set[str] = set()
    nicks: set[str] = set()
    search_token: str | None = None

    for acc_id in account_ids:
        token = get_token(acc_id)
        if not token:
            continue
        if not search_token:
            search_token = token
        for acc in list_accounts_public():
            if acc["id"] == acc_id and acc.get("name"):
                nicks.add(str(acc["name"]).strip())
        args = ["manage", "get-user-info", "--json"]
        if guild_id:
            args.extend(["--guild-id", guild_id])
        try:
            _, output = run_cli(token, args, timeout=15)
            parsed = _parse_json_output(output)
            if parsed:
                nicks |= _nicknames_from_user_info(parsed)
                ids |= _collect_numeric_user_ids(parsed)
        except Exception:
            pass

    if guild_id and search_token:
        for nick in nicks:
            if not nick or nick in _GENERIC_POSTER_NICKS:
                continue
            ids |= _search_member_tinyids(search_token, guild_id, nick)

    _own_poster_cache[cache_key] = (time.time(), ids)
    return ids


def _parse_feeds(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("feeds", "list", "items"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    inner = data.get("data")
    if isinstance(inner, list):
        return [x for x in inner if isinstance(x, dict)]
    if isinstance(inner, dict):
        for key in ("feeds", "data", "list"):
            val = inner.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _summarize_cli_error(output: str, exc: Exception | None = None) -> str:
    if exc is not None:
        msg = str(exc).lower()
        if "timeout" in msg:
            return "请求超时"
        return "网络异常"
    text = (output or "").strip()
    if not text:
        return "空响应"
    parsed = _parse_json_output(text)
    if isinstance(parsed, dict):
        for key in ("message", "msg", "error", "detail"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:120]
        code = parsed.get("code")
        if code not in (None, 0, "0"):
            return f"API 错误 code={code}"
    return text[-120:]


def get_feeds(token: str, guild_id: str, channel_id: str, count: int = 20) -> tuple[list[dict], str]:
    try:
        rc, output = run_cli(token, [
            "feed", "get-channel-timeline-feeds",
            "--guild-id", guild_id,
            "--channel-id", channel_id,
            "--count", str(count),
            "--json",
        ], timeout=25)
    except Exception as e:
        return [], _summarize_cli_error("", e)
    if rc != 0 and not output.strip():
        return [], f"CLI 退出码 {rc}"
    parsed = _parse_json_output(output)
    feeds = _parse_feeds(parsed)
    if feeds:
        return feeds, ""
    if isinstance(parsed, dict) and parsed.get("success") is True:
        return [], "API 返回空帖子列表（可能限流或并发冲突）"
    return [], _summarize_cli_error(output)


def get_feeds_with_rotation(
    tokens: list[str],
    guild_id: str,
    channel_id: str,
    count: int = 20,
    *,
    retries: int = 5,
) -> tuple[list[dict], str]:
    last_err = ""
    if not tokens:
        return [], "没有可用账号 Token"
    for attempt in range(max(1, retries)):
        if attempt > 0:
            time.sleep(min(15, 2 + attempt * 2) + random.uniform(0, 2))
        indices = list(range(len(tokens)))
        random.shuffle(indices)
        # 每轮只抽部分账号试探，避免高并发时 40+ 次 CLI 连环失败
        sample = indices[: min(12, len(indices))]
        for idx in sample:
            feeds, err = get_feeds(tokens[idx], guild_id, channel_id, count)
            if feeds:
                return feeds, ""
            if err:
                last_err = err
    return [], last_err or "所有账号均无法读取帖子（可能并发限流，将自动重试）"


def like_feed(token: str, guild_id: str, channel_id: str, feed_id: str) -> tuple[bool, str]:
    try:
        _, output = run_cli(token, [
            "feed", "do-feed-prefer",
            "--guild-id", guild_id,
            "--channel-id", channel_id,
            "--feed-id", feed_id,
            "--action", "1",
            "--json", "--yes",
        ], timeout=15)
    except Exception as e:
        msg = str(e).lower()
        if "timeout" in msg:
            return False, "timeout"
        return False, "network"

    if '"success":true' in output or '"code":0' in output:
        return True, ""
    if any(m in output for m in RATE_LIMIT_MARKERS):
        return False, "rate_limit"
    if any(m in output for m in OIDB_LIMIT_MARKERS):
        return False, "oidb_limit"
    return False, "other"


def run_auto_like_for_channel(channel_cfg: dict) -> dict[str, Any]:
    """对单个频道配置执行一轮自动点赞。"""
    guild_id = str(channel_cfg.get("guild_id", ""))
    channel_id = str(channel_cfg.get("channel_id", ""))
    if not guild_id or not channel_id:
        return {"ok": False, "error": "频道 ID 不完整", "total_likes": 0, "feeds_processed": 0}

    account_ids = channel_cfg.get("account_ids") or list_all_account_ids()
    tokens: list[str] = []
    for acc_id in account_ids:
        token = get_token(acc_id)
        if token:
            tokens.append(token)
    if not tokens:
        return {"ok": False, "error": "没有可用账号 Token", "total_likes": 0, "feeds_processed": 0}

    likes_min = max(1, int(channel_cfg.get("likes_min") or 1))
    likes_max = max(likes_min, int(channel_cfg.get("likes_max") or likes_min))
    likes_max = min(likes_max, len(tokens))
    only_own = bool(channel_cfg.get("only_own_posts", True))
    last_ts = float(channel_cfg.get("last_liked_at") or 0)
    feeds_per_channel = int(channel_cfg.get("feeds_per_channel") or 20)

    feeds, fetch_err = get_feeds_with_rotation(tokens, guild_id, channel_id, feeds_per_channel)
    if not feeds:
        msg = f"读取帖子失败：{fetch_err}" if fetch_err else "无帖子或读取失败"
        return {
            "ok": True,
            "fetch_failed": True,
            "message": msg,
            "total_likes": 0,
            "feeds_processed": 0,
            "last_liked_at": last_ts,
            "logs": [msg],
        }

    candidate = [f for f in feeds if _parse_feed_time(f) > last_ts]

    own_ids: set[str] = set()
    if only_own:
        own_ids = resolve_own_poster_ids(account_ids, guild_id)
        if not own_ids:
            msg = "无法解析本系统账号 ID，已跳过点赞（请检查账号 Token）"
            return {
                "ok": True,
                "message": msg,
                "total_likes": 0,
                "feeds_processed": 0,
                "last_liked_at": last_ts,
                "logs": [msg],
            }

    target_feeds: list[dict] = []
    for feed in candidate:
        if only_own and not (_extract_poster_ids(feed) & own_ids):
            continue
        target_feeds.append(feed)

    total_likes = 0
    total_errors = 0
    processed = 0
    logs: list[str] = []

    for feed in target_feeds:
        feed_id = str(feed.get("id") or feed.get("feed_id") or "")
        if not feed_id:
            continue
        title = (feed.get("title") or feed.get("content_snippet") or feed.get("content") or "")[:40]
        num_likes = random.randint(likes_min, likes_max)
        logs.append(f"帖子 {title or feed_id}：目标 {num_likes} 赞")

        indices = list(range(len(tokens)))
        random.shuffle(indices)
        success = 0

        for i in range(min(num_likes, len(tokens))):
            token = tokens[indices[i]]
            ok, err_type = like_feed(token, guild_id, channel_id, feed_id)
            if ok:
                success += 1
                total_likes += 1
            else:
                total_errors += 1
                if err_type == "oidb_limit":
                    logs.append("OIDB 全局限流，跳过此帖")
                    break
                if err_type == "rate_limit":
                    time.sleep(random.uniform(3, 6))
                    continue
            time.sleep(random.uniform(1.5, 3.5))

        logs.append(f"完成 {success}/{num_likes} 赞")
        processed += 1

    new_last = last_ts
    if processed > 0:
        for feed in target_feeds:
            new_last = max(new_last, _parse_feed_time(feed))

    msg = f"扫描 {len(candidate)} 条新帖，点赞 {processed} 帖，共 {total_likes} 次"
    if not candidate:
        msg = "没有新帖子"
    elif only_own and not target_feeds and candidate:
        msg = f"有 {len(candidate)} 条新帖，但无本系统账号发布的内容"

    return {
        "ok": True,
        "message": msg,
        "total_likes": total_likes,
        "total_errors": total_errors,
        "feeds_processed": processed,
        "feeds_seen": len(candidate),
        "last_liked_at": new_last,
        "logs": logs,
    }
