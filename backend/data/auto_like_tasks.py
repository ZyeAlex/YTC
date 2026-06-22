"""自动点赞任务持久化：cache/auto_like_tasks.json"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from backend.config import CACHE_DIR

log = logging.getLogger(__name__)

TASKS_FILE = CACHE_DIR / "auto_like_tasks.json"
TASKS_BACKUP = TASKS_FILE.with_suffix(".json.bak")
LEGACY_USER_DIR = CACHE_DIR / "users"

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_migrated = False


def _default_task() -> dict[str, Any]:
    return {
        "guild_id": "",
        "channel_id": "",
        "name": "",
        "enabled": False,
        "likes_min": 1,
        "likes_max": 5,
        "schedule_cron": "",
        "only_own_posts": True,
        "account_ids": [],
        "feeds_per_channel": 20,
        "last_liked_at": 0,
        "next_run_at": 0,
        "last_run_message": "",
        "last_run_at": 0,
        "run_logs": [],
    }


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize_task(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    task = _default_task()
    task.update({k: v for k, v in item.items() if k in task})
    if isinstance(task.get("run_logs"), list):
        task["run_logs"] = [
            x for x in task["run_logs"][-100:]
            if isinstance(x, dict) and x.get("message")
        ]
    else:
        task["run_logs"] = []
    guild_id = str(task.get("guild_id", "")).strip()
    channel_id = str(task.get("channel_id", "")).strip()
    if not guild_id or not channel_id:
        return None
    task["guild_id"] = guild_id
    task["channel_id"] = channel_id
    return task


def _normalize_store(data: dict[str, Any]) -> dict[str, Any]:
    tasks = []
    for item in data.get("tasks") or []:
        task = _normalize_task(item)
        if task:
            tasks.append(task)
    return {"tasks": tasks}


def _write_atomic(data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if TASKS_FILE.exists():
        shutil.copy2(TASKS_FILE, TASKS_BACKUP)
    tmp = TASKS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(TASKS_FILE)


def _migrate_legacy_once() -> None:
    global _migrated
    if _migrated or TASKS_FILE.exists():
        _migrated = True
        return
    _migrated = True
    if LEGACY_USER_DIR.exists():
        for user_dir in sorted(LEGACY_USER_DIR.iterdir()):
            if not user_dir.is_dir():
                continue
            legacy = user_dir / "auto_like_tasks.json"
            if legacy.exists():
                try:
                    data = _read_json(legacy)
                    if isinstance(data, dict):
                        store = _normalize_store(data)
                        _write_atomic(store)
                        log.info("已从 %s 迁移自动点赞任务", legacy)
                        return
                except Exception as exc:
                    log.warning("迁移自动点赞任务失败 %s: %s", legacy, exc)
    legacy_root = CACHE_DIR / "auto_like_tasks.json"
    if legacy_root.exists():
        try:
            data = _read_json(legacy_root)
            if isinstance(data, dict):
                _write_atomic(_normalize_store(data))
        except Exception:
            pass


def _load_unlocked(*, reload: bool = False) -> dict[str, Any]:
    global _cache
    _migrate_legacy_once()
    if not reload and _cache is not None:
        return _cache

    for path in (TASKS_FILE, TASKS_BACKUP):
        if not path.exists():
            continue
        try:
            data = _read_json(path)
            if isinstance(data, dict):
                _cache = _normalize_store(data)
                return _cache
        except Exception as exc:
            log.warning("读取自动点赞任务失败 %s: %s", path, exc)

    _cache = {"tasks": []}
    return _cache


def _mutate(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    global _cache
    with _lock:
        store = _load_unlocked(reload=True)
        mutator(store)
        store = _normalize_store(store)
        _write_atomic(store)
        _cache = store
        return deepcopy(store)


def _tasks_to_api(store: dict[str, Any]) -> dict[str, Any]:
    tasks = store.get("tasks") or []
    return {
        "enabled": any(bool(t.get("enabled")) for t in tasks),
        "channels": deepcopy(tasks),
    }


def get_auto_like_config() -> dict[str, Any]:
    with _lock:
        return _tasks_to_api(_load_unlocked())


def save_auto_like_config(auto_like: dict[str, Any]) -> dict[str, Any]:
    existing = {
        f"{t['guild_id']}:{t['channel_id']}": t
        for t in get_auto_like_config().get("channels", [])
    }
    merged: list[dict[str, Any]] = []
    for item in auto_like.get("channels") or []:
        task = _normalize_task(item if isinstance(item, dict) else {})
        if not task:
            continue
        key = f"{task['guild_id']}:{task['channel_id']}"
        old = existing.get(key, {})
        for persist_key in (
            "last_liked_at", "next_run_at", "last_run_message", "last_run_at", "run_logs",
        ):
            if persist_key in old and persist_key not in item:
                task[persist_key] = old.get(persist_key, task.get(persist_key))
        merged.append(task)

    def _patch(store: dict[str, Any]) -> None:
        store["tasks"] = merged

    _mutate(_patch)
    return get_auto_like_config()


def upsert_auto_like_channel(channel: dict[str, Any]) -> dict[str, Any]:
    from backend.schedule import seconds_until_next

    guild_id = str(channel.get("guild_id", "")).strip()
    channel_id = str(channel.get("channel_id", "")).strip()
    if not guild_id or not channel_id:
        raise ValueError("guild_id 和 channel_id 不能为空")

    item = _default_task()
    item.update({k: v for k, v in channel.items() if k in item or k == "name"})
    item["guild_id"] = guild_id
    item["channel_id"] = channel_id
    if item.get("likes_max", 0) < item.get("likes_min", 1):
        item["likes_max"] = item["likes_min"]

    cron = (item.get("schedule_cron") or "").strip()

    def _patch(store: dict[str, Any]) -> None:
        tasks = list(store.get("tasks") or [])
        found = False
        old: dict[str, Any] = {}
        idx = -1
        for i, t in enumerate(tasks):
            if t.get("guild_id") == guild_id and t.get("channel_id") == channel_id:
                found = True
                old = t
                idx = i
                break

        was_enabled = bool(old.get("enabled"))
        now_enabled = bool(item.get("enabled"))
        cron_changed = (old.get("schedule_cron") or "").strip() != cron
        patched = deepcopy(item)

        if now_enabled and not was_enabled:
            patched["next_run_at"] = time.time()
            last_msg = str(old.get("last_run_message") or "")
            if "共 0 次" in last_msg or "无本系统账号" in last_msg or "没有新帖子" in last_msg:
                patched["last_liked_at"] = 0
        elif found and now_enabled and was_enabled and not cron_changed:
            patched["next_run_at"] = old.get("next_run_at", patched.get("next_run_at", 0))
        elif cron:
            _, next_ts = seconds_until_next(cron)
            patched["next_run_at"] = next_ts

        if found:
            for persist_key in (
                "last_liked_at", "last_run_message", "last_run_at", "run_logs",
            ):
                if persist_key in old and persist_key not in channel:
                    patched[persist_key] = old[persist_key]
            tasks[idx] = patched
        else:
            tasks.append(patched)
        store["tasks"] = tasks

    _mutate(_patch)
    return get_auto_like_config()


def update_auto_like_channel_state(
    guild_id: str,
    channel_id: str,
    **fields: Any,
) -> None:
    def _patch(store: dict[str, Any]) -> None:
        for t in store.get("tasks") or []:
            if t.get("guild_id") == guild_id and t.get("channel_id") == channel_id:
                t.update(fields)
                break

    _mutate(_patch)


def append_auto_like_run_log(
    guild_id: str,
    channel_id: str,
    message: str,
    level: str = "info",
) -> None:
    def _patch(store: dict[str, Any]) -> None:
        for t in store.get("tasks") or []:
            if t.get("guild_id") == guild_id and t.get("channel_id") == channel_id:
                logs = list(t.get("run_logs") or [])
                logs.append({
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "level": level,
                    "message": message,
                })
                t["run_logs"] = logs[-100:]
                break

    _mutate(_patch)
