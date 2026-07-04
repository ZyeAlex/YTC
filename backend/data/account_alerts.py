"""系统告警持久化：cache/account_alerts.json（账号异常 + 下载失败）"""

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

ERROR_LABELS: dict[int, str] = {
    10023: "无权限（未加入频道）",
    890500: "账号被封禁",
    91001: "下载超时",
    91002: "下载失败",
    91003: "B站风控",
    91004: "视频超限",
    91005: "视频不可下载",
    91006: "代理错误",
}

ACCOUNT_ALERT_CODES = frozenset({10023, 890500})
DOWNLOAD_ALERT_CODES = frozenset({91001, 91002, 91003, 91004, 91005, 91006})


def _default_store() -> dict[str, Any]:
    return {"alerts": []}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _is_download_code(code: int) -> bool:
    return code in DOWNLOAD_ALERT_CODES


def classify_download_error(message: str) -> int:
    """根据错误文案归类下载告警码。"""
    from backend.services.download import should_skip_download
    from backend.services.proxy_bypass import is_proxy_error

    text = message or ""
    if "下载超时" in text or "下载流超时" in text:
        return 91001
    if is_proxy_error(text) or "代理错误" in text:
        return 91006
    if "v_voucher" in text or "风控验证" in text or "风控" in text:
        return 91003
    if "超过" in text and "MB" in text:
        return 91004
    if should_skip_download(text):
        return 91005
    if any(kw in text for kw in ("连接 B 站超时", "连接 b 站超时", "网络连接 B 站不稳定", "IncompleteRead", "Operation too slow")):
        return 91002
    return 91002


def _normalize_alert(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    code = int(item.get("error_code") or 0)
    if code not in ERROR_LABELS:
        return None

    kind = str(item.get("kind") or ("download" if _is_download_code(code) else "account"))
    base = {
        "id": str(item.get("id") or uuid.uuid4().hex[:12]),
        "ts": int(item.get("ts") or time.time()),
        "error_code": code,
        "error_label": ERROR_LABELS[code],
        "message": str(item.get("message") or "")[:240],
        "task_id": str(item.get("task_id") or ""),
        "kind": kind,
    }

    if kind == "download" or _is_download_code(code):
        task_id = str(item.get("task_id") or "").strip()
        video_id = str(item.get("video_id") or item.get("guild_id") or "").strip()
        if not task_id or not video_id:
            return None
        platform = str(item.get("platform") or "")
        title = str(item.get("video_title") or item.get("channel_name") or "").strip()
        return {
            **base,
            "kind": "download",
            "platform": platform,
            "video_id": video_id,
            "video_title": title,
            "channel_name": title,
            "account_id": "-",
            "account_label": {"bili": "B站", "douyin": "抖音"}.get(platform, platform or "下载"),
            "guild_id": video_id,
            "channel_id": "-",
        }

    account_id = str(item.get("account_id") or "").strip()
    guild_id = str(item.get("guild_id") or "").strip()
    channel_id = str(item.get("channel_id") or "").strip()
    if not account_id or not guild_id or not channel_id:
        return None
    return {
        **base,
        "kind": "account",
        "account_id": account_id,
        "account_label": str(item.get("account_label") or account_id),
        "guild_id": guild_id,
        "channel_id": channel_id,
        "channel_name": str(item.get("channel_name") or ""),
        "platform": "",
        "video_id": "",
        "video_title": "",
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


def _append_alert_record(alert: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_alert(alert)
    if normalized is None:
        raise ValueError("invalid alert payload")
    with _lock:
        store = _load_store()
        alerts = [normalized] + list(store.get("alerts") or [])
        store = _normalize_store({"alerts": alerts})
        _write_store(store)
        global _cache
        _cache = store
    return normalized


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
    if error_code not in ACCOUNT_ALERT_CODES:
        raise ValueError(f"unsupported account error_code: {error_code}")
    return _append_alert_record({
        "id": uuid.uuid4().hex[:12],
        "ts": int(time.time()),
        "kind": "account",
        "error_code": error_code,
        "account_id": account_id,
        "account_label": account_label,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "message": (message or "")[:240],
        "task_id": task_id,
    })


def append_download_alert(
    *,
    task_id: str,
    platform: str,
    video_id: str,
    video_title: str = "",
    message: str = "",
    error_code: int | None = None,
) -> dict[str, Any]:
    code = error_code if error_code is not None else classify_download_error(message)
    if code not in DOWNLOAD_ALERT_CODES:
        code = 91002
    return _append_alert_record({
        "id": uuid.uuid4().hex[:12],
        "ts": int(time.time()),
        "kind": "download",
        "error_code": code,
        "task_id": task_id,
        "platform": platform,
        "video_id": video_id,
        "video_title": (video_title or "")[:120],
        "channel_name": (video_title or "")[:120],
        "message": (message or "")[:240],
    })


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
