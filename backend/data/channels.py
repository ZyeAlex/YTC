from __future__ import annotations

from backend.data.user_data import get_channels as _get_channels
from backend.data.user_data import save_channels as _save_channels


def load_channels() -> list[dict]:
    return _get_channels()


def get_channels() -> list[dict]:
    return load_channels()


def save_channel_order(ordered: list[dict]) -> list[dict]:
    """按前端传入顺序重排并写入配置"""
    channels = load_channels()
    key_to_ch = {(c["guild_id"], c["channel_id"]): c for c in channels}
    new_list: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for item in ordered:
        key = (item["guild_id"], item["channel_id"])
        if key in key_to_ch:
            new_list.append(key_to_ch[key])
            seen.add(key)

    for ch in channels:
        key = (ch["guild_id"], ch["channel_id"])
        if key not in seen:
            new_list.append(ch)

    return _save_channels(new_list)
