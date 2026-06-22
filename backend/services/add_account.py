from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator, Iterator
from typing import Any

from backend.data.user_data import get_accounts, get_channels, save_accounts
from backend.services.join_channel import join_guild_account

TOKEN_PREFIX = "bot:v1_"
JOIN_GAP_MIN = 1.0
JOIN_GAP_MAX = 3.0


def _normalize_token(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        raise ValueError("Token 不能为空")
    if not token.startswith(TOKEN_PREFIX):
        raise ValueError(f"Token 格式无效，应以 {TOKEN_PREFIX} 开头")
    return token


def _next_index(accounts: list[dict]) -> int:
    indices: list[int] = []
    for acc in accounts:
        try:
            indices.append(int(acc.get("index", 0)))
        except (TypeError, ValueError):
            continue
    return max(indices, default=0) + 1


def _token_exists(accounts_data: dict[str, Any], token: str) -> bool:
    for key in ("qq_accounts", "bot_accounts"):
        for acc in accounts_data.get(key, []):
            if str(acc.get("token", "")).strip() == token:
                return True
    return False


def _unique_guilds(channels: list[dict]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for ch in channels:
        guild_id = str(ch.get("guild_id", "")).strip()
        if not guild_id or guild_id in seen:
            continue
        seen.add(guild_id)
        name = str(ch.get("name", "")).strip() or guild_id
        out.append((guild_id, name))
    return out


def normalize_account_entries(
    raw_entries: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if not raw_entries:
        raise ValueError("请填写至少一个账号")

    entries: list[dict[str, str]] = []
    for i, item in enumerate(raw_entries, 1):
        token_raw = str(item.get("token", "")).strip()
        if not token_raw:
            continue
        kind = str(item.get("account_type", "bot")).strip().lower()
        if kind not in ("qq", "bot"):
            raise ValueError(f"第 {i} 个账号：类型无效")
        name = str(item.get("name", "")).strip()
        token = _normalize_token(token_raw)
        if kind == "qq" and not name:
            raise ValueError(f"第 {i} 个账号：QQ 必须填写名称")
        entries.append({"name": name, "token": token, "kind": kind})

    if not entries:
        raise ValueError("请填写至少一个有效 Token")

    tokens = [e["token"] for e in entries]
    if len(tokens) != len(set(tokens)):
        raise ValueError("存在重复 Token")

    return entries


def _prepare_batch(
    entries: list[dict[str, str]],
) -> tuple[list[dict], list[dict], list[dict[str, Any]], list[tuple[str, str]]]:
    accounts_data = get_accounts()
    qq_accounts = list(accounts_data.get("qq_accounts", []))
    bot_accounts = list(accounts_data.get("bot_accounts", []))

    for entry in entries:
        if _token_exists({"qq_accounts": qq_accounts, "bot_accounts": bot_accounts}, entry["token"]):
            suffix = entry["token"][-16:]
            raise ValueError(f"Token 已存在于账号列表：…{suffix}")

    prepared: list[dict[str, Any]] = []
    temp_qq = list(qq_accounts)
    temp_bot = list(bot_accounts)
    for entry in entries:
        kind = entry["kind"]
        if kind == "qq":
            index = _next_index(temp_qq)
            display = entry["name"]
            temp_qq.append({"index": index})
        else:
            index = _next_index(temp_bot)
            display = entry["name"] or f"Bot-{index}"
            temp_bot.append({"index": index})
        prepared.append({
            "token": entry["token"],
            "index": index,
            "name": display,
            "kind": kind,
        })

    guilds = _unique_guilds(get_channels())
    return qq_accounts, bot_accounts, prepared, guilds


async def _join_account_guilds(
    token: str,
    display_name: str,
    guilds: list[tuple[str, str]],
    account_index: int,
    accounts_total: int,
) -> AsyncIterator[dict[str, Any]]:
    total = len(guilds)
    join_results: list[dict[str, Any]] = []
    ok_count = 0

    yield {
        "type": "start",
        "total": total,
        "name": display_name,
        "account_index": account_index,
        "accounts_total": accounts_total,
    }

    for i, (guild_id, guild_name) in enumerate(guilds):
        yield {
            "type": "joining",
            "index": i + 1,
            "total": total,
            "channel": guild_name,
            "guild_id": guild_id,
            "account_index": account_index,
            "accounts_total": accounts_total,
            "account_name": display_name,
        }

        result = await asyncio.to_thread(join_guild_account, token, guild_id)
        result["guild_id"] = guild_id
        result["channel"] = guild_name
        join_results.append(result)
        if result.get("ok"):
            ok_count += 1

        yield {
            "type": "joined",
            "index": i + 1,
            "total": total,
            "channel": guild_name,
            "guild_id": guild_id,
            "ok": bool(result.get("ok")),
            "error": result.get("error") or "",
            "joined": ok_count,
            "account_index": account_index,
            "accounts_total": accounts_total,
            "account_name": display_name,
        }

        if i + 1 < total:
            gap = random.uniform(JOIN_GAP_MIN, JOIN_GAP_MAX)
            next_name = guilds[i + 1][1]
            yield {
                "type": "wait",
                "seconds": round(gap, 1),
                "next": next_name,
                "index": i + 1,
                "total": total,
                "account_index": account_index,
                "accounts_total": accounts_total,
                "account_name": display_name,
            }
            await asyncio.sleep(gap)

    yield {
        "type": "_join_summary",
        "join_results": join_results,
        "joined": ok_count,
        "total": total,
    }


async def add_accounts_events_async(
    entries: list[dict[str, str]],
) -> AsyncIterator[dict[str, Any]]:
    try:
        qq_accounts, bot_accounts, prepared, guilds = _prepare_batch(entries)
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return

    accounts_total = len(prepared)
    guilds_total = len(guilds)
    yield {
        "type": "batch_start",
        "accounts_total": accounts_total,
        "guilds_total": guilds_total,
    }

    added_accounts: list[dict[str, Any]] = []
    skipped_accounts: list[dict[str, Any]] = []
    saved: dict[str, Any] = {"qq_accounts": qq_accounts, "bot_accounts": bot_accounts}

    for ai, item in enumerate(prepared, 1):
        kind = item["kind"]
        token = item["token"]
        display_name = item["name"]
        index = item["index"]

        yield {
            "type": "account_start",
            "account_index": ai,
            "accounts_total": accounts_total,
            "name": display_name,
            "account_id": f"{kind}:{index}",
        }

        join_results: list[dict[str, Any]] = []
        ok_count = 0
        guild_total = len(guilds)

        async for event in _join_account_guilds(
            token, display_name, guilds, ai, accounts_total,
        ):
            if event.get("type") == "_join_summary":
                join_results = event.get("join_results") or []
                ok_count = int(event.get("joined") or 0)
                guild_total = int(event.get("total") or 0)
                continue
            yield event

        if guilds and ok_count == 0:
            first_err = next((r.get("error") for r in join_results if r.get("error")), "未知错误")
            skipped_accounts.append({
                "name": display_name,
                "account_id": f"{kind}:{index}",
                "error": first_err,
            })
            yield {
                "type": "account_skipped",
                "account_index": ai,
                "accounts_total": accounts_total,
                "name": display_name,
                "error": first_err,
            }
            continue

        new_account = {"index": index, "name": display_name, "token": token}
        if kind == "qq":
            qq_accounts.append(new_account)
        else:
            bot_accounts.append(new_account)

        saved = save_accounts({
            "qq_accounts": qq_accounts,
            "bot_accounts": bot_accounts,
        })

        account_info = {
            "id": f"{kind}:{index}",
            "type": kind,
            "index": index,
            "name": display_name,
        }
        added_accounts.append(account_info)

        yield {
            "type": "account_done",
            "account_index": ai,
            "accounts_total": accounts_total,
            "account": account_info,
            "accounts": saved,
            "join_results": join_results,
            "joined": ok_count,
            "total": guild_total,
        }

    if not added_accounts:
        yield {"type": "error", "message": "没有账号添加成功"}
        return

    yield {
        "type": "batch_done",
        "added": len(added_accounts),
        "skipped": len(skipped_accounts),
        "accounts_total": accounts_total,
        "added_accounts": added_accounts,
        "skipped_accounts": skipped_accounts,
        "accounts": saved,
    }


def add_account(
    entries: list[dict[str, str]],
) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for event in add_accounts_events(entries):
        if event.get("type") == "error":
            raise ValueError(str(event.get("message") or "添加失败"))
        if event.get("type") == "batch_done":
            last = event
    if not last:
        raise ValueError("添加失败")
    return {"ok": True, **last}


def add_accounts_events(
    entries: list[dict[str, str]],
) -> Iterator[dict[str, Any]]:
    import time

    try:
        qq_accounts, bot_accounts, prepared, guilds = _prepare_batch(entries)
    except ValueError as e:
        yield {"type": "error", "message": str(e)}
        return

    accounts_total = len(prepared)
    yield {
        "type": "batch_start",
        "accounts_total": accounts_total,
        "guilds_total": len(guilds),
    }

    added_accounts: list[dict[str, Any]] = []
    skipped_accounts: list[dict[str, Any]] = []
    saved: dict[str, Any] = {"qq_accounts": qq_accounts, "bot_accounts": bot_accounts}

    for ai, item in enumerate(prepared, 1):
        kind = item["kind"]
        token = item["token"]
        display_name = item["name"]
        index = item["index"]

        yield {
            "type": "account_start",
            "account_index": ai,
            "accounts_total": accounts_total,
            "name": display_name,
        }

        join_results: list[dict[str, Any]] = []
        ok_count = 0
        total = len(guilds)

        for i, (guild_id, guild_name) in enumerate(guilds):
            result = join_guild_account(token, guild_id)
            result["guild_id"] = guild_id
            result["channel"] = guild_name
            join_results.append(result)
            if result.get("ok"):
                ok_count += 1
            if i + 1 < total:
                time.sleep(random.uniform(JOIN_GAP_MIN, JOIN_GAP_MAX))

        if guilds and ok_count == 0:
            first_err = next((r.get("error") for r in join_results if r.get("error")), "未知错误")
            skipped_accounts.append({"name": display_name, "error": first_err})
            yield {"type": "account_skipped", "name": display_name, "error": first_err}
            continue

        new_account = {"index": index, "name": display_name, "token": token}
        if kind == "qq":
            qq_accounts.append(new_account)
        else:
            bot_accounts.append(new_account)
        saved = save_accounts({
            "qq_accounts": qq_accounts,
            "bot_accounts": bot_accounts,
        })
        added_accounts.append({"id": f"{kind}:{index}", "name": display_name, "type": kind})
        yield {"type": "account_done", "account_index": ai, "accounts_total": accounts_total, "account": added_accounts[-1], "accounts": saved}

    if not added_accounts:
        yield {"type": "error", "message": "没有账号添加成功"}
        return

    yield {
        "type": "batch_done",
        "added": len(added_accounts),
        "skipped": len(skipped_accounts),
        "accounts": saved,
        "added_accounts": added_accounts,
        "skipped_accounts": skipped_accounts,
    }


async def stream_add_account_sse_async(
    entries: list[dict[str, str]],
) -> AsyncIterator[str]:
    async for event in add_accounts_events_async(entries):
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
