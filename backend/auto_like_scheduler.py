from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from backend.data.auto_like_tasks import (
    append_auto_like_run_log,
    get_auto_like_config,
    update_auto_like_channel_state,
)
from backend.schedule import seconds_until_next
from backend.services.auto_like import run_auto_like_for_channel

_scheduler_task: asyncio.Task | None = None
_recent_logs: list[dict[str, Any]] = []
_MAX_LOGS = 100
_running_channels: set[str] = set()
_channel_start_lock = threading.Lock()
_run_semaphore = threading.Semaphore(1)
_FETCH_RETRY_SECONDS = 180


def get_auto_like_status() -> dict[str, Any]:
    cfg = get_auto_like_config()
    return {
        "enabled": cfg.get("enabled"),
        "channels": cfg.get("channels", []),
        "running": list(_running_channels),
        "logs": list(reversed(_recent_logs[-30:])),
    }


def _log(channel_name: str, guild_id: str, channel_id: str, message: str, level: str = "info"):
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "level": level,
        "channel": channel_name or f"{guild_id}:{channel_id}",
        "message": message,
    }
    _recent_logs.append(entry)
    if len(_recent_logs) > _MAX_LOGS:
        del _recent_logs[: len(_recent_logs) - _MAX_LOGS]
    try:
        append_auto_like_run_log(guild_id, channel_id, message, level)
    except Exception:
        pass


def _channel_key(guild_id: str, channel_id: str) -> str:
    return f"{guild_id}:{channel_id}"


def _should_run_channel(ch: dict) -> bool:
    if not ch.get("enabled"):
        return False
    cron = (ch.get("schedule_cron") or "").strip()
    if not cron:
        return False
    now = time.time()
    next_at = float(ch.get("next_run_at") or 0)
    if next_at <= 0:
        _, next_at = seconds_until_next(cron)
        update_auto_like_channel_state(
            ch["guild_id"], ch["channel_id"], next_run_at=next_at,
        )
        return False
    return now >= next_at


def _reserve_channel_run(ch: dict) -> bool:
    key = _channel_key(ch["guild_id"], ch["channel_id"])
    with _channel_start_lock:
        if key in _running_channels:
            return False
        if not _should_run_channel(ch):
            return False
        cron = (ch.get("schedule_cron") or "").strip()
        _, next_at = seconds_until_next(cron)
        update_auto_like_channel_state(
            ch["guild_id"], ch["channel_id"], next_run_at=next_at,
        )
        _running_channels.add(key)
        return True


def _release_channel_run(guild_id: str, channel_id: str) -> None:
    key = _channel_key(guild_id, channel_id)
    with _channel_start_lock:
        _running_channels.discard(key)


def _run_channel_sync(ch: dict) -> None:
    guild_id = ch["guild_id"]
    channel_id = ch["channel_id"]
    name = ch.get("name") or _channel_key(guild_id, channel_id)
    _run_semaphore.acquire()
    try:
        _log(name, guild_id, channel_id, "开始自动点赞")
        result = run_auto_like_for_channel(ch)
        cron = (ch.get("schedule_cron") or "").strip()
        now = time.time()
        if result.get("fetch_failed"):
            retry_at = now + _FETCH_RETRY_SECONDS
            _, cron_next = seconds_until_next(cron) if cron else (0, 0)
            next_at = retry_at if not cron_next else min(retry_at, cron_next)
            _log(name, guild_id, channel_id, f"读帖失败，{_FETCH_RETRY_SECONDS // 60} 分钟后重试", "warn")
        else:
            _, next_at = seconds_until_next(cron)
        update_auto_like_channel_state(
            guild_id,
            channel_id,
            last_liked_at=result.get("last_liked_at", ch.get("last_liked_at", 0)),
            next_run_at=next_at,
            last_run_at=now,
            last_run_message=result.get("message", ""),
        )
        level = "info" if result.get("ok") else "warn"
        _log(name, guild_id, channel_id, result.get("message", "完成"), level)
        for line in result.get("logs") or []:
            _log(name, guild_id, channel_id, line)
    except Exception as e:
        _log(name, guild_id, channel_id, f"异常: {e}", "error")
        cron = (ch.get("schedule_cron") or "").strip()
        if cron:
            update_auto_like_channel_state(
                guild_id, channel_id,
                next_run_at=time.time() + _FETCH_RETRY_SECONDS,
            )
    finally:
        _run_semaphore.release()
        _release_channel_run(guild_id, channel_id)


async def run_channel_now(guild_id: str, channel_id: str) -> dict[str, Any]:
    cfg = get_auto_like_config()
    target = None
    for ch in cfg.get("channels", []):
        if ch.get("guild_id") == guild_id and ch.get("channel_id") == channel_id:
            target = dict(ch)
            break
    if not target:
        raise ValueError("频道配置不存在")
    key = _channel_key(guild_id, channel_id)
    with _channel_start_lock:
        if key in _running_channels:
            raise ValueError("该频道正在点赞中，请稍候")
        _running_channels.add(key)
    try:
        await asyncio.to_thread(_run_channel_sync, target)
    except Exception:
        _release_channel_run(guild_id, channel_id)
        raise
    return {"ok": True}


def _dispatch_channel(ch: dict) -> None:
    ch_copy = dict(ch)
    if not _reserve_channel_run(ch_copy):
        return
    asyncio.create_task(asyncio.to_thread(_run_channel_sync, ch_copy))


async def _scheduler_loop():
    while True:
        try:
            await asyncio.sleep(20)
            cfg = get_auto_like_config()
            for ch in cfg.get("channels", []):
                _dispatch_channel(ch)
        except asyncio.CancelledError:
            break
        except Exception:
            pass


def start_auto_like_scheduler():
    global _scheduler_task
    loop = asyncio.get_running_loop()
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = loop.create_task(_scheduler_loop())


async def stop_auto_like_scheduler():
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
