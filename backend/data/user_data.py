"""发帖账号与频道：存于 config/config.json"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.data.app_config import (
    get_accounts_data,
    get_channels_data,
    init_storage,
    save_accounts_data,
    save_channels_data,
)


def init_user_data() -> None:
    init_storage()


def get_accounts() -> dict[str, Any]:
    return get_accounts_data()


def get_channels() -> list[dict]:
    return get_channels_data()


def save_accounts(accounts: dict[str, Any]) -> dict[str, Any]:
    return save_accounts_data(accounts)


def save_channels(channels: list[dict]) -> list[dict]:
    return save_channels_data(channels)


def load_user_data() -> dict[str, Any]:
    return {
        "accounts": deepcopy(get_accounts_data()),
        "channels": deepcopy(get_channels_data()),
    }


def save_user_data(data: dict[str, Any]) -> dict[str, Any]:
    accounts = save_accounts_data(data.get("accounts", {}))
    channels = save_channels_data(data.get("channels", []))
    return {"accounts": accounts, "channels": channels}
