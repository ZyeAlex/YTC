"""全局配置：config/config.json（B站 Cookie、过滤词、抖音 API 等）"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from backend.config import CONFIG_DIR
from backend.data.bili_cookie import normalize_bili_config, normalize_bili_cookie_value
from backend.services.video_filter import DEFAULT_FILTER_PATTERNS

log = logging.getLogger(__name__)

CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_TEMPLATE = CONFIG_DIR / "config.template.json"
CONFIG_BACKUP = CONFIG_FILE.with_suffix(".json.bak")

LEGACY_BILI = CONFIG_DIR / "bili_cookie.json"
LEGACY_BILI_NETSCAPE = CONFIG_DIR / "bili_cookie_netscape.txt"
LEGACY_FILTER = CONFIG_DIR / "filter_patterns.json"
LEGACY_USERS_DIR = CONFIG_DIR / "users"

_storage_migrated = False

_DEFAULT: dict[str, Any] = {
    "access_token": "",
    "guaikei_api_token": "",
    "bili": {
        "cookies": [],
    },
    "douyin": {
        "cookies": [],
    },
    "accounts": {
        "qq_accounts": [],
        "bot_accounts": [],
    },
    "channels": [],
    "filter_patterns": list(DEFAULT_FILTER_PATTERNS),
}

_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _read_netscape_cookie(path: Path) -> str:
    text = _read_text(path)
    if not text:
        return ""
    has_data = any(
        "\t" in ln and not ln.lstrip().startswith("#")
        for ln in text.splitlines()
    )
    return text if has_data else ""


def _merge_legacy(base: dict[str, Any]) -> dict[str, Any]:
    cfg = deepcopy(base)
    bili_cookie = ""

    legacy_bili = _read_json(LEGACY_BILI)
    if isinstance(legacy_bili, dict) and legacy_bili.get("cookie"):
        bili_cookie = normalize_bili_cookie_value(str(legacy_bili["cookie"]))

    netscape = _read_netscape_cookie(LEGACY_BILI_NETSCAPE)
    if netscape and not bili_cookie:
        bili_cookie = normalize_bili_cookie_value(netscape)

    cfg["bili"] = {"cookies": [bili_cookie] if bili_cookie else []}
    cfg["douyin"] = {"cookies": []}

    patterns_data = _read_json(LEGACY_FILTER)
    if isinstance(patterns_data, dict):
        patterns = patterns_data.get("patterns", [])
        if isinstance(patterns, list) and patterns:
            cfg["filter_patterns"] = [str(p).strip() for p in patterns if str(p).strip()]

    if not cfg.get("guaikei_api_token"):
        cfg["guaikei_api_token"] = os.environ.get("GUAIKEI_API_TOKEN", "")

    return cfg


def _init_config_from_template() -> bool:
    if CONFIG_FILE.exists():
        return False
    if not CONFIG_TEMPLATE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CONFIG_TEMPLATE, CONFIG_FILE)
    log.info("已从 config.template.json 创建 config/config.json")
    return True


def _normalize_accounts(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"qq_accounts": [], "bot_accounts": []}
    qq = raw.get("qq_accounts") if isinstance(raw.get("qq_accounts"), list) else []
    bot = raw.get("bot_accounts") if isinstance(raw.get("bot_accounts"), list) else []
    return {"qq_accounts": qq, "bot_accounts": bot}


def _normalize_channels(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        guild_id = str(item.get("guild_id", "")).strip()
        channel_id = str(item.get("channel_id", "")).strip()
        if not guild_id or not channel_id:
            continue
        out.append({
            "name": str(item.get("name", "")).strip(),
            "guild_id": guild_id,
            "channel_id": channel_id,
            "category": str(item.get("category", "游戏")).strip() or "游戏",
        })
    return out


def _migrate_legacy_user_storage(cfg: dict[str, Any]) -> dict[str, Any]:
    accounts = cfg.get("accounts") if isinstance(cfg.get("accounts"), dict) else {}
    channels = cfg.get("channels") if isinstance(cfg.get("channels"), list) else []
    has_accounts = bool(accounts.get("qq_accounts") or accounts.get("bot_accounts"))
    has_channels = bool(channels)
    if has_accounts and has_channels:
        return cfg

    source: Path | None = None
    preferred = LEGACY_USERS_DIR / "Zye.json"
    if preferred.exists():
        source = preferred
    elif LEGACY_USERS_DIR.exists():
        for path in sorted(LEGACY_USERS_DIR.glob("*.json")):
            if path.name.endswith(".bak"):
                continue
            source = path
            break

    legacy = _read_json(source) if source else None
    if not isinstance(legacy, dict):
        return cfg

    out = deepcopy(cfg)
    if not has_accounts and isinstance(legacy.get("accounts"), dict):
        out["accounts"] = _normalize_accounts(legacy["accounts"])
    if not has_channels and isinstance(legacy.get("channels"), list):
        out["channels"] = _normalize_channels(legacy["channels"])
    if out != cfg:
        log.info("已从 %s 迁移账号与频道到 config.json", source.name if source else "legacy")
    return out


def _normalize_douyin_config(douyin: dict) -> dict[str, list[str]]:
    cookies: list[str] = []
    if isinstance(douyin.get("cookies"), list):
        cookies = [str(c).strip() for c in douyin["cookies"] if str(c).strip()]
    legacy = str(douyin.get("cookie", "")).strip()
    if legacy and legacy not in cookies:
        cookies.insert(0, legacy)
    return {"cookies": cookies}


def _normalize_loaded(data: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(_DEFAULT)
    merged.update({k: v for k, v in data.items() if k in _DEFAULT})
    if isinstance(data.get("bili"), dict):
        merged["bili"] = normalize_bili_config(data["bili"])
    elif not merged["bili"].get("cookie"):
        legacy_ns = _read_netscape_cookie(LEGACY_BILI_NETSCAPE)
        if legacy_ns:
            merged["bili"] = normalize_bili_config({"download_cookie_netscape": legacy_ns})
    if isinstance(data.get("douyin"), dict):
        merged["douyin"] = _normalize_douyin_config(data["douyin"])
    if isinstance(data.get("filter_patterns"), list):
        merged["filter_patterns"] = data["filter_patterns"]
    if isinstance(data.get("accounts"), dict):
        merged["accounts"] = _normalize_accounts(data["accounts"])
    if isinstance(data.get("channels"), list):
        merged["channels"] = _normalize_channels(data["channels"])
    merged["access_token"] = str(data.get("access_token") or merged.get("access_token") or "").strip()
    return _migrate_legacy_user_storage(merged)


def _bili_config_changed(before: dict, after: dict) -> bool:
    old_bili = before.get("bili") if isinstance(before.get("bili"), dict) else {}
    new_bili = after.get("bili") if isinstance(after.get("bili"), dict) else {}
    return normalize_bili_config(old_bili).get("cookies") != normalize_bili_config(new_bili).get("cookies")


def _read_config_from_disk() -> dict[str, Any] | None:
    for path in (CONFIG_FILE, CONFIG_BACKUP):
        if not path.exists():
            continue
        try:
            raw = _read_json(path)
            if not isinstance(raw, dict):
                continue
            normalized = _normalize_loaded(raw)
            if _bili_config_changed(raw, normalized):
                _write_config_atomic(normalized)
                log.info("已迁移 B站 Cookie 为 bili.cookies 数组")
            return normalized
        except Exception as exc:
            log.warning("读取配置失败 %s: %s", path, exc)
    return None


def _write_config_atomic(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        shutil.copy2(CONFIG_FILE, CONFIG_BACKUP)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def _load_config_unlocked(*, reload: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is not None and not reload:
        return _cache

    if not CONFIG_FILE.exists() and not CONFIG_BACKUP.exists():
        _init_config_from_template()

    loaded = _read_config_from_disk()
    if loaded is not None:
        migrated = _migrate_legacy_user_storage(loaded)
        if migrated != loaded:
            _write_config_atomic(migrated)
            loaded = migrated
        _cache = loaded
        return _cache

    if _cache is not None:
        log.warning("config.json 无法解析，使用内存中的上次有效配置")
        return _cache

    cfg = _merge_legacy(deepcopy(_DEFAULT))
    if CONFIG_FILE.exists():
        log.error("config.json 损坏且无可用备份，仅合并 legacy 文件，不覆盖原文件")
        _cache = cfg
        return _cache

    _write_config_atomic(cfg)
    _cache = cfg
    return _cache


def load_config(*, reload: bool = False) -> dict[str, Any]:
    with _lock:
        return deepcopy(_load_config_unlocked(reload=reload))


def save_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    global _cache
    with _lock:
        data = deepcopy(cfg if cfg is not None else _load_config_unlocked())
        if isinstance(data.get("bili"), dict):
            data["bili"] = normalize_bili_config(data["bili"])
        if isinstance(data.get("douyin"), dict):
            data["douyin"] = _normalize_douyin_config(data["douyin"])
        if isinstance(data.get("accounts"), dict):
            data["accounts"] = _normalize_accounts(data["accounts"])
        if isinstance(data.get("channels"), list):
            data["channels"] = _normalize_channels(data["channels"])
        data["access_token"] = str(data.get("access_token") or "").strip()
        _write_config_atomic(data)
        _cache = data
        return deepcopy(data)


def _mutate_config(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    global _cache
    with _lock:
        cfg = _load_config_unlocked(reload=True)
        mutator(cfg)
        if isinstance(cfg.get("bili"), dict):
            cfg["bili"] = normalize_bili_config(cfg["bili"])
        if isinstance(cfg.get("douyin"), dict):
            cfg["douyin"] = _normalize_douyin_config(cfg["douyin"])
        if isinstance(cfg.get("accounts"), dict):
            cfg["accounts"] = _normalize_accounts(cfg["accounts"])
        if isinstance(cfg.get("channels"), list):
            cfg["channels"] = _normalize_channels(cfg["channels"])
        cfg["access_token"] = str(cfg.get("access_token") or "").strip()
        _write_config_atomic(cfg)
        _cache = cfg
        return deepcopy(cfg)


def get_access_token() -> str:
    return str(load_config().get("access_token", "")).strip()


def get_accounts_data() -> dict[str, Any]:
    accounts = load_config().get("accounts", {})
    if not isinstance(accounts, dict):
        return {"qq_accounts": [], "bot_accounts": []}
    return deepcopy(_normalize_accounts(accounts))


def save_accounts_data(accounts: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_accounts(accounts)

    def _patch(cfg: dict[str, Any]) -> None:
        cfg["accounts"] = normalized

    _mutate_config(_patch)
    return normalized


def get_channels_data() -> list[dict]:
    channels = load_config().get("channels", [])
    if not isinstance(channels, list):
        return []
    return deepcopy(_normalize_channels(channels))


def save_channels_data(channels: list[dict]) -> list[dict]:
    normalized = _normalize_channels(channels)

    def _patch(cfg: dict[str, Any]) -> None:
        cfg["channels"] = normalized

    _mutate_config(_patch)
    return normalized


def init_storage() -> None:
    global _storage_migrated
    if _storage_migrated:
        return
    _storage_migrated = True
    _load_config_unlocked(reload=True)


def get_guaikei_token() -> str:
    token = os.environ.get("GUAIKEI_API_TOKEN") or load_config().get("guaikei_api_token", "")
    return str(token).strip()


def get_bili_cookies() -> list[str]:
    bili = load_config(reload=True).get("bili", {})
    if not isinstance(bili, dict):
        return []
    return list(normalize_bili_config(bili).get("cookies") or [])


def get_bili_cookie() -> str:
    cookies = get_bili_cookies()
    return cookies[0] if cookies else ""


def save_bili_cookies(cookies: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in cookies:
        value = normalize_bili_cookie_value(str(raw))
        if value and value not in cleaned:
            cleaned.append(value)

    def _patch(cfg: dict[str, Any]) -> None:
        cfg["bili"] = {"cookies": cleaned}

    _mutate_config(_patch)
    return cleaned


def save_douyin_cookies(cookies: list[str]) -> list[str]:
    cleaned = [str(c).strip() for c in cookies if str(c).strip()]

    def _patch(cfg: dict[str, Any]) -> None:
        cfg["douyin"] = _normalize_douyin_config({"cookies": cleaned})

    _mutate_config(_patch)
    return cleaned


def get_douyin_cookies() -> list[str]:
    douyin = load_config(reload=True).get("douyin", {})
    if not isinstance(douyin, dict):
        return []
    normalized = _normalize_douyin_config(douyin)
    return list(normalized.get("cookies") or [])


def get_douyin_cookie() -> str:
    cookies = get_douyin_cookies()
    return cookies[0] if cookies else ""


def get_filter_patterns_data() -> list[str]:
    patterns = load_config().get("filter_patterns", [])
    if isinstance(patterns, list) and patterns:
        return [str(p).strip() for p in patterns if str(p).strip()]
    return list(DEFAULT_FILTER_PATTERNS)


def save_filter_patterns_data(patterns: list[str]) -> list[str]:
    cleaned = [p.strip() for p in patterns if p and p.strip()]

    def _patch(cfg: dict[str, Any]) -> None:
        cfg["filter_patterns"] = cleaned

    _mutate_config(_patch)
    return cleaned
