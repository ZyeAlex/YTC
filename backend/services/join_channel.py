from __future__ import annotations

import json
import re
import time
from typing import Any

from backend.data.accounts import get_token, list_accounts_public
from backend.data.user_data import get_channels, save_channels
from backend.services.cli_env import run_cli

PD_URL_RE = re.compile(r"https?://pd\.qq\.com/s/\w+", re.I)
NAME_RE = re.compile(r"【([^】]+)】")
HALL_NAMES = ("全部", "广场", "大厅", "全部频道")
JOIN_GAP_SEC = 0.6


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


def extract_share_url(text: str) -> str:
    match = PD_URL_RE.search(text or "")
    if not match:
        raise ValueError("未找到 pd.qq.com 分享链接")
    return match.group(0)


def extract_channel_name(text: str, fallback: str = "") -> str:
    match = NAME_RE.search(text or "")
    if match:
        return match.group(1).strip()
    return fallback.strip()


def _cli_data(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return {}
    data = parsed.get("data")
    return data if isinstance(data, dict) else parsed


def _extract_channel_list(parsed: Any) -> list[dict]:
    if not isinstance(parsed, dict):
        return []
    data = parsed.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("channels", "channel_list", "list", "items"):
            val = data.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _first_token() -> str:
    for acc in list_accounts_public():
        token = get_token(acc["id"])
        if token:
            return token
    raise ValueError("没有可用账号 Token")


def get_share_info(token: str, url: str) -> dict[str, str]:
    _, output = run_cli(
        token,
        ["manage", "get-share-info", "--url", url, "--json"],
        timeout=30,
    )
    parsed = _parse_json_output(output)
    if not isinstance(parsed, dict) or not parsed.get("success"):
        raise ValueError("解析分享链接失败，请检查链接是否有效")

    data = _cli_data(parsed)
    guild_id = str(data.get("guild_id") or "").strip()
    if not guild_id:
        raise ValueError("分享链接中未找到 guild_id")

    guild_name = str(
        data.get("guild_name") or data.get("name") or data.get("title") or ""
    ).strip()
    return {"guild_id": guild_id, "guild_name": guild_name}


def find_default_channel_id(token: str, guild_id: str) -> str:
    _, output = run_cli(
        token,
        ["manage", "get-guild-channel-list", "--guild-id", guild_id, "--json"],
        timeout=30,
    )
    parsed = _parse_json_output(output)
    channels = _extract_channel_list(parsed)
    if not channels:
        raise ValueError("无法获取频道版块列表")

    for ch in channels:
        name = str(ch.get("channel_name") or ch.get("name") or "").strip()
        if name in HALL_NAMES:
            channel_id = str(ch.get("channel_id") or ch.get("id") or "").strip()
            if channel_id:
                return channel_id

    first = channels[0]
    channel_id = str(first.get("channel_id") or first.get("id") or "").strip()
    if not channel_id:
        raise ValueError("未找到默认大厅频道")
    return channel_id


def join_guild_account(token: str, guild_id: str) -> dict[str, Any]:
    _, output = run_cli(
        token,
        ["manage", "join-guild", "--guild-id", guild_id, "--json", "--yes"],
        timeout=60,
    )
    parsed = _parse_json_output(output)
    if isinstance(parsed, dict):
        inner = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        action = inner.get("action")
        if action == "need_verification":
            msg = str(inner.get("message") or "需要回答问题或管理员审核才能加入")
            return {"ok": False, "error": msg, "need_verification": True}
        if parsed.get("success") is True:
            return {"ok": True}

    lowered = output.lower()
    if '"success":true' in output or '"code":0' in output:
        return {"ok": True}
    if "已加入" in output or "already" in lowered:
        return {"ok": True, "already": True}
    if '"code":20063' in output or "错误码 20063" in output:
        return {"ok": False, "error": "限流，请稍后重试"}

    snippet = output.strip().replace("\n", " ")[:180]
    return {"ok": False, "error": snippet or "加入失败"}


def add_channel_from_share(text: str, category: str) -> dict[str, Any]:
    url = extract_share_url(text)
    category = (category or "游戏").strip() or "游戏"

    accounts = list_accounts_public()
    if not accounts:
        raise ValueError("没有配置发帖账号")

    probe_token = _first_token()
    share = get_share_info(probe_token, url)
    guild_id = share["guild_id"]
    name = extract_channel_name(text, share.get("guild_name") or "")
    channel_id = find_default_channel_id(probe_token, guild_id)

    existing = get_channels()
    for ch in existing:
        if ch["guild_id"] == guild_id and ch["channel_id"] == channel_id:
            raise ValueError(f"频道已存在：{ch.get('name') or name}")

    join_results: list[dict[str, Any]] = []
    ok_count = 0
    for i, acc in enumerate(accounts):
        token = get_token(acc["id"])
        if not token:
            join_results.append({
                "account": acc["name"],
                "account_id": acc["id"],
                "ok": False,
                "error": "无 Token",
            })
            continue

        result = join_guild_account(token, guild_id)
        result["account"] = acc["name"]
        result["account_id"] = acc["id"]
        join_results.append(result)
        if result.get("ok"):
            ok_count += 1
        if i + 1 < len(accounts):
            time.sleep(JOIN_GAP_SEC)

    if ok_count == 0:
        first_err = next((r.get("error") for r in join_results if r.get("error")), "未知错误")
        raise ValueError(f"所有账号加入失败：{first_err}")

    new_channel = {
        "name": name or share.get("guild_name") or guild_id,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "category": category,
    }
    saved = save_channels(existing + [new_channel])

    return {
        "ok": True,
        "channel": new_channel,
        "channels": saved,
        "join_results": join_results,
        "joined": ok_count,
        "total": len(accounts),
    }
