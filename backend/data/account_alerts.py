"""账号异常告警持久化：cache/account_alerts.json"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backend.config import CACHE_DIR

log = logging.getLogger(__name__)

ALERTS_FILE = CACHE_DIR / "account_alerts.json"
ALERTS_BACKUP = ALERTS_FILE.with_suffix(".json.bak")
MAX_ALERTS = 500

_lock = threading.Lock()
_cache: dict[str, Any] | None = None

ERROR_LABELS = {
    10023: "无权限（未加入频道）",
    890500: "账号被封禁",
}


def _default_store() -> dict[str, Any]:
    return {"alerts": []}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize_alert(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    code = int(item.get("error_code") or 0)
    if code not in ERROR_LABELS:
        return None
    account_id = str(item.get("account_id") or "").strip()
    guild_id = str(item.get("guild_id") or "").strip()
    channel_id = str(item.get("channel_id") or "").strip()
    if not account_id or not guild_id or not channel_id:
        return None
    return {
        "id": str(item.get("id") or uuid.uuid4().hex[:12]),
        "ts": int(item.get("ts") or time.time()),
        "error_code": code,
        "error_label": ERROR_LABELS[code],
        "account_id": account_id,
        "account_label": str(item.get("account_label") or account_id),
        "guild_id": guild_id,
        "channel_id": channel_id,
        "channel_name": str(item.get("channel_name") or ""),
        "message": str(item.get("message") or "")[:240],
        "task_id": str(item.get("task_id") or ""),
    }


def _normalize_store(data: dict[str, Any]) -> dict[str, Any]:
    alerts = []
    for item in data.get("alerts") or []:
        alert = _normalize_alert(item)
        if alert:
            alerts.append(alert)
    alerts.sort(key=lambda x: x["ts"], reverse=True)
    return {"alerts": alerts[:MAX_ALERTS]}


def _write_store(data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ALERTS_FILE.with_suffix(".json.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
    if ALERTS_FILE.exists():
        shutil.copy2(ALERTS_FILE, ALERTS_BACKUP)
    tmp.replace(ALERTS_FILE)


def _load_store() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    raw = _read_json(ALERTS_FILE)
    if raw is None and ALERTS_BACKUP.exists():
        raw = _read_json(ALERTS_BACKUP)
    if not isinstance(raw, dict):
        raw = _default_store()
    _cache = _normalize_store(raw)
    return _cache


def append_alert(
    *,
    error_code: int,
    account_id: str,
    account_label: str,
    guild_id: str,
    channel_id: str,
    channel_name: str = "",
    message: str = "",
    task_id: str = "",
) -> dict[str, Any]:
    if error_code not in ERROR_LABELS:
        raise ValueError(f"unsupported error_code: {error_code}")
    alert = {
        "id": uuid.uuid4().hex[:12],
        "ts": int(time.time()),
        "error_code": error_code,
        "error_label": ERROR_LABELS[error_code],
        "account_id": account_id,
        "account_label": account_label,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "message": (message or "")[:240],
        "task_id": task_id,
    }
    with _lock:
        store = _load_store()
        alerts = [alert] + list(store.get("alerts") or [])
        store = _normalize_store({"alerts": alerts})
        _write_store(store)
        global _cache
        _cache = store
    return alert


def list_alerts(*, limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        store = _load_store()
    alerts = list(store.get("alerts") or [])
    cap = max(1, min(limit, MAX_ALERTS))
    return alerts[:cap]


def clear_alerts() -> None:
    with _lock:
        store = _default_store()
        _write_store(store)
        global _cache
        _cache = store
