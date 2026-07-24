from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from backend.config import CACHE_DIR, DOWNLOAD_WORKER_TIMEOUT
from backend.data.account_alerts import append_alert, append_download_alert, classify_download_error
from backend.data.accounts import list_accounts_public
from backend.schedule import (
    cron_from_legacy_interval,
    describe_cron,
    format_next_time,
    seconds_until_next,
    stagger_start_minute,
    validate_cron,
)
from backend.services.download import is_valid_local_video, prepare_output_path, should_skip_download
from backend.services.download_manager import download_manager
from backend.services.join_channel import format_auto_join_message, join_guild_for_account
from backend.services.publish import account_label, pick_random_account, publish_video
from backend.services.search_service import search_videos
from backend.services.video_order import (
    SOURCE_COLLECTION,
    prev_send_index,
    send_order_indices,
    send_sequence_pos,
    sends_newest_last,
)

CHANNEL_INTERVAL_MIN = 60
CHANNEL_INTERVAL_MAX = 180
POST_SLOW_WARN_MS = 60_000
POST_RETRY_SLEEP = {
    "permission": (3, 6),
    "rate_limit": (12, 20),
    "banned": (12, 20),
    "bot_abnormal": (1, 3),
    "token_expired": (1, 3),
}

OLD_RATIO_PAUSE_12H = 0.30
OLD_RATIO_PAUSE_24H = 0.70
PAUSE_12H_SECONDS = 12 * 3600
PAUSE_24H_SECONDS = 24 * 3600
SEARCH_ERROR_PAUSE_SECONDS = 5 * 60
_SEARCH_COOLDOWN_MINUTES_RE = re.compile(
    r"try\s+again\s+in\s+(\d+)\s*minutes?",
    re.IGNORECASE,
)

MAX_VIDEO_DOWNLOAD_FAILURES = 3
DOWNLOAD_RETRY_COOLDOWN = 300
STALE_DOWNLOAD_SECONDS = max(DOWNLOAD_WORKER_TIMEOUT.values()) + 60
RETRYABLE_ERR_TYPES = frozenset({
    "rate_limit", "permission", "banned", "bot_abnormal", "token_expired",
})
RETRY_REASON_LABEL = {
    "rate_limit": "限流",
    "permission": "无权限",
    "banned": "账号封禁",
    "bot_abnormal": "Bot 状态异常",
    "token_expired": "Token 过期",
}
ALERT_ERR_CODES = {
    "permission": 10023,
    "banned": 890500,
    "bot_abnormal": 100063,
    "token_expired": 100051,
}


@dataclass
class FetchBatchResult:
    added: int = 0
    pause_until: float = 0.0
    pause_reason: str = ""

STATUS_CREATED = "created"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

TASK_TYPE_ONCE = "once"
TASK_TYPE_RECURRING = "recurring"
TASK_TYPE_CUSTOM = "custom"
SOURCE_SEARCH = "search"
SOURCE_COLLECTION = "collection"
SOURCE_MANUAL = "manual"
MAX_TASK_VIDEOS = 500

VIDEO_PENDING = "pending"
VIDEO_WAITING = "waiting"
VIDEO_DOWNLOADING = "downloading"
VIDEO_POSTING = "posting"
VIDEO_DONE = "done"
VIDEO_SKIPPED = "skipped"
VIDEO_FAILED = "failed"

CH_PENDING = "pending"
CH_POSTING = "posting"
CH_DONE = "done"
CH_FAILED = "failed"


from backend.services.proxy_bypass import is_proxy_error
from backend.data.account_alerts import list_abnormal_account_ids


def _pick_post_account(
    account_ids: list[str],
    exclude: set[str] | None = None,
    *,
    prefer_qq: bool = False,
) -> str:
    tried = set(exclude or set())
    # 避开近期 100063 / 封禁等账号告警（优先用可用 QQ / 健康 bot）
    abnormal = list_abnormal_account_ids(within_sec=7 * 24 * 3600)
    tried |= abnormal
    candidates = [a for a in account_ids if a not in tried]
    if not candidates:
        # 告警账号占满时退回：只排除本轮已试
        candidates = [a for a in account_ids if a not in (exclude or set())] or list(account_ids)
    if prefer_qq:
        qq = [a for a in candidates if a.startswith("qq:")]
        if qq:
            return random.choice(qq)
    # 无 prefer_qq 时也优先 QQ，减少抽到已挂 bot
    qq = [a for a in candidates if a.startswith("qq:")]
    bots = [a for a in candidates if a.startswith("bot:")]
    if qq and bots:
        # 约 70% 走 QQ，保证健康 bot 仍有机会轮换
        pool = qq if random.random() < 0.7 else bots
        return random.choice(pool)
    return random.choice(candidates)


def _repair_proxy_skipped_videos(task: TaskState) -> int:
    """把因代理错误误标为跳过的视频恢复为待发送。"""
    fixed = 0
    for vp in task.video_progress:
        if vp.get("status") != VIDEO_SKIPPED:
            continue
        if not is_proxy_error(vp.get("message", "")):
            continue
        vp["status"] = VIDEO_PENDING
        vp["account"] = ""
        vp["message"] = ""
        vp["started_at"] = ""
        vp["sent_at"] = ""
        vp.pop("wait_until", None)
        for ch in vp.get("channels", []):
            ch["status"] = CH_PENDING
            ch["sent_at"] = ""
        fixed += 1
    if fixed:
        done = sum(1 for v in task.video_progress if v.get("status") == VIDEO_DONE)
        skipped = sum(1 for v in task.video_progress if v.get("status") == VIDEO_SKIPPED)
        pending = sum(
            1 for v in task.video_progress
            if v.get("status") not in (VIDEO_DONE, VIDEO_SKIPPED, VIDEO_FAILED)
        )
        task.result = {
            **task.result,
            "videos_total": len(task.video_progress),
            "videos_done": done,
            "videos_skipped": skipped,
        }
        if pending:
            task.status = STATUS_PAUSED
            task.finished_at = ""
        _log(task, "info", f"已恢复 {fixed} 条因代理错误误跳过的视频")
    return fixed


ACTIVE_TASKS_FILE = CACHE_DIR / "tasks.json"
LEGACY_TASKS_FILE = CACHE_DIR / "tasks.json"
LEGACY_USER_TASKS_DIR = CACHE_DIR / "users"


@dataclass
class TaskState:
    task_id: str
    name: str
    status: str = STATUS_CREATED
    platform: str = ""
    keyword: str = ""
    video_count: int = 0
    channel_count: int = 0
    schedule_cron: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    logs: list[dict] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)
    video_progress: list[dict] = field(default_factory=list)


_tasks: dict[str, TaskState] = {}
_cancelled: set[str] = set()
_paused: set[str] = set()
_active_loops: set[str] = set()
_task_run_locks: dict[str, asyncio.Lock] = {}
_watchdog_task: asyncio.Task | None = None
_video_process_locks: dict[tuple[str, int], asyncio.Lock] = {}


def _stop_reason(task_id: str) -> str | None:
    if task_id not in _tasks or task_id in _cancelled:
        return "delete"
    if task_id in _paused:
        return "pause"
    return None


def _aborted(task_id: str) -> bool:
    return _stop_reason(task_id) is not None


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _task_alive(task_id: str) -> bool:
    return task_id in _tasks and task_id not in _cancelled and task_id not in _paused


def _apply_pause(task: TaskState):
    task.status = STATUS_PAUSED
    task.finished_at = ""
    for vp in task.video_progress:
        if vp.get("status") in (VIDEO_WAITING, VIDEO_DOWNLOADING, VIDEO_POSTING):
            vp["status"] = VIDEO_PENDING
            vp["message"] = "已暂停"
    _log(task, "warn", "任务已暂停，点击继续执行可恢复")
    _persist_tasks()


def _log(task: TaskState, level: str, message: str):
    if not _task_alive(task.task_id):
        return
    task.logs.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
    })
    if len(task.logs) > 500:
        task.logs = task.logs[-500:]
    _persist_tasks()


def _init_video_progress(videos: list[dict], channels: list[dict]) -> list[dict]:
    ch_tpl = [{"name": c.get("name", ""), "status": CH_PENDING, "sent_at": ""} for c in channels]
    return [
        {
            "id": v.get("id", ""),
            "title": v.get("title", ""),
            "pic": v.get("pic", ""),
            "author": v.get("author", ""),
            "status": VIDEO_PENDING,
            "account": "",
            "message": "",
            "started_at": "",
            "sent_at": "",
            "download_failures": 0,
            "download_retry_until": 0,
            "channels": [dict(c) for c in ch_tpl],
        }
        for v in videos
    ]


def _reset_video_progress(task: TaskState):
    videos = task.payload.get("videos", [])
    channels = task.payload.get("channels", [])
    task.video_progress = _init_video_progress(videos, channels)


def _ensure_video_progress(task: TaskState):
    """保留已有发送进度，仅补齐缺失条目（不覆盖已发送状态）"""
    videos = task.payload.get("videos", [])
    channels = task.payload.get("channels", [])
    if not videos:
        task.video_progress = []
        return

    by_id = {v.get("id"): v for v in task.video_progress if v.get("id")}
    merged: list[dict] = []
    for v in videos:
        vid = v.get("id", "")
        if vid in by_id:
            merged.append(by_id[vid])
        else:
            merged.append(_init_video_progress([v], channels)[0])
    task.video_progress = merged


def _video_is_terminal(vp: dict) -> bool:
    return vp.get("status") in (VIDEO_DONE, VIDEO_SKIPPED)


def _video_download_cooldown(vp: dict) -> bool:
    until = float(vp.get("download_retry_until") or 0)
    return until > time.time()


def _is_stale_downloading(vp: dict) -> bool:
    if vp.get("status") != VIDEO_DOWNLOADING:
        return False
    started = vp.get("started_at", "")
    if not started:
        return True
    try:
        t = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - t).total_seconds() > STALE_DOWNLOAD_SECONDS
    except ValueError:
        return True


def _reset_stale_downloading_item(task: TaskState, vi: int) -> bool:
    vp = task.video_progress[vi]
    if not _is_stale_downloading(vp):
        return False
    _set_video(task, vi, status=VIDEO_PENDING, message="下载中断，继续重试")
    vp["started_at"] = ""
    return True


def _video_auto_skip(vp: dict) -> bool:
    """自动任务轮次中不再重试的状态（含发帖/下载失败）。"""
    return vp.get("status") in (VIDEO_DONE, VIDEO_SKIPPED, VIDEO_FAILED)


def _video_all_channels_done(vp: dict) -> bool:
    chs = vp.get("channels", [])
    return bool(chs) and all(c.get("status") == CH_DONE for c in chs)


def _video_had_successful_post(vp: dict) -> bool:
    return any(c.get("status") == CH_DONE for c in vp.get("channels", []))


def _get_task_cron(task: TaskState) -> str:
    return (task.payload.get("schedule_cron") or task.schedule_cron or "").strip()


def _apply_schedule_to_payload(payload: dict) -> dict:
    cron = (payload.get("schedule_cron") or "").strip()
    if not cron:
        payload["schedule_cron"] = ""
        return payload
    if not validate_cron(cron):
        raise ValueError("Cron 表达式无效")
    payload["schedule_cron"] = cron
    return payload


def _sync_task_schedule_fields(task: TaskState):
    task.schedule_cron = _get_task_cron(task)
    task.payload["schedule_cron"] = task.schedule_cron


def _is_partial_video_resume(vp: dict) -> bool:
    return vp.get("status") == VIDEO_POSTING and not _video_all_channels_done(vp)


def _needs_cron_wait(task: TaskState, vi: int, vp: dict) -> bool:
    if not _get_task_cron(task):
        return False
    if _is_partial_video_resume(vp):
        return False
    if vp.get("status") == VIDEO_DOWNLOADING:
        return False
    # 发送队列中的第一条，或上一条未成功发帖（跳过/失败）时立即处理，不占用 cron 档位
    if prev_send_index(_task_send_indices(task), vi) is None or not _prev_video_posted_successfully(task, vi):
        return False
    return True


def _cron_stored_wait_is_valid(cron: str, wait_until: float) -> bool:
    """重启续等：仅当储存时间与 cron 下一档一致时才复用 wait_until。"""
    if time.time() >= wait_until:
        return False
    _, next_ts = seconds_until_next(cron)
    return float(wait_until) <= next_ts + 1


async def _wait_for_cron_slot(
    task_id: str, task: TaskState, vi: int, *, action: str = "发送"
) -> bool:
    cron = _get_task_cron(task)
    if not cron:
        return _stop_reason(task_id) is None

    vp = task.video_progress[vi]
    wait_sec, next_ts = seconds_until_next(cron)
    wait_until = vp.get("wait_until")
    if (
        vp.get("status") == VIDEO_WAITING
        and wait_until
        and _cron_stored_wait_is_valid(cron, float(wait_until))
    ):
        next_ts = float(wait_until)
        wait_sec = max(0, int(next_ts - time.time()))

    next_label = format_next_time(next_ts)
    _set_video(
        task,
        vi,
        status=VIDEO_WAITING,
        message=f"等待 {next_label} {action}",
        wait_until=next_ts,
    )
    _log(task, "info", f"⏳ 等待到 {next_label} {action}（{describe_cron(cron)}）")
    return await _interruptible_sleep(wait_sec, task_id, task)


def _task_send_indices(task: TaskState) -> list[int]:
    payload = task.payload
    n = len(task.video_progress) or len(payload.get("videos", []))
    return send_order_indices(
        n,
        source=payload.get("source", SOURCE_SEARCH),
        search_sort=payload.get("search_sort", "recent"),
    )


def _prev_video_posted_successfully(task: TaskState, vi: int) -> bool:
    prev_vi = prev_send_index(_task_send_indices(task), vi)
    if prev_vi is None:
        return False
    prev = task.video_progress[prev_vi]
    return prev.get("status") == VIDEO_DONE and _video_had_successful_post(prev)


def _recover_stale_downloading(task: TaskState) -> bool:
    """下载中状态超时（进程崩溃/协程丢失）时恢复为 pending。"""
    active_path = download_manager.active_output_path if download_manager.is_busy() else None
    recovered = False
    videos = task.payload.get("videos", [])
    for vi, vp in enumerate(task.video_progress):
        if not _is_stale_downloading(vp):
            continue
        if active_path and vi < len(videos):
            video = videos[vi]
            path = prepare_output_path(
                task.payload.get("platform", ""),
                video.get("id", ""),
                video.get("title", ""),
            )
            if path == active_path:
                continue
        if _reset_stale_downloading_item(task, vi):
            recovered = True
    if recovered:
        _log(task, "info", "检测到下载卡住，已重置为待重试")
        _persist_tasks()
    return recovered


def _recover_stale_waits(task: TaskState) -> bool:
    """等待时间已过（含系统休眠）或 cron 时间错误的视频恢复为 pending。"""
    now = time.time()
    cron = _get_task_cron(task)
    recovered = False
    for vi, vp in enumerate(task.video_progress):
        if vp.get("status") != VIDEO_WAITING:
            continue
        if not _prev_video_posted_successfully(task, vi):
            vp["status"] = VIDEO_PENDING
            vp["message"] = "上一条未发帖，继续执行"
            vp.pop("wait_until", None)
            recovered = True
            continue
        wait_until = vp.get("wait_until")
        if wait_until is None or now >= float(wait_until):
            vp["status"] = VIDEO_PENDING
            vp["message"] = "休眠恢复，继续执行" if wait_until else "继续执行"
            vp.pop("wait_until", None)
            recovered = True
        elif cron and not _cron_stored_wait_is_valid(cron, float(wait_until)):
            vp["status"] = VIDEO_PENDING
            vp["message"] = "等待时间已校正，继续执行"
            vp.pop("wait_until", None)
            recovered = True
    if recovered:
        _persist_tasks()
    _recover_stale_downloading(task)
    return recovered


def _set_video(task: TaskState, vi: int, **kwargs):
    new_status = kwargs.get("status")
    terminal = new_status in (VIDEO_DONE, VIDEO_FAILED, VIDEO_SKIPPED)
    if not _task_alive(task.task_id) and not terminal:
        return
    if vi < 0 or vi >= len(task.video_progress):
        return
    entry = task.video_progress[vi]
    if new_status == VIDEO_DOWNLOADING:
        kwargs["started_at"] = _now_str()
    if new_status in (VIDEO_DONE, VIDEO_FAILED, VIDEO_SKIPPED):
        kwargs["sent_at"] = _now_str()
    entry.update(kwargs)
    _persist_tasks()


def _record_download_alert(task: TaskState, vi: int, err_msg: str, *, note: str = "") -> None:
    try:
        payload = task.payload
        platform = str(payload.get("platform") or "")
        videos = payload.get("videos") or []
        video = videos[vi] if vi < len(videos) else {}
        vp = task.video_progress[vi] if vi < len(task.video_progress) else {}
        video_id = str(video.get("id") or vp.get("id") or "")
        title = str(video.get("title") or vp.get("title") or "")[:120]
        if not video_id:
            return
        parts = [p for p in (note, err_msg) if p]
        message = " · ".join(parts)[:240]
        append_download_alert(
            task_id=task.task_id,
            platform=platform,
            video_id=video_id,
            video_title=title,
            message=message,
            error_code=classify_download_error(message),
        )
    except Exception:
        pass


def _mark_download_skipped(task: TaskState, vi: int, err_msg: str, *, permanent: bool = False) -> None:
    title = task.video_progress[vi].get("title", "")[:30]
    msg = (err_msg or "下载失败")[:80]
    if permanent:
        _set_video(task, vi, status=VIDEO_SKIPPED, message=f"下载失败，已跳过: {msg[:60]}")
        _log(task, "warn", f"下载失败，已跳过 — {title}")
        _record_download_alert(task, vi, msg, note="永久跳过")
        return
    vp = task.video_progress[vi]
    fails = int(vp.get("download_failures") or 0) + 1
    vp["download_failures"] = fails
    if fails >= MAX_VIDEO_DOWNLOAD_FAILURES:
        _set_video(
            task,
            vi,
            status=VIDEO_SKIPPED,
            message=f"多次下载失败({fails})，已跳过: {msg[:40]}",
        )
        _log(task, "warn", f"多次下载失败，已跳过 — {title}")
        _record_download_alert(task, vi, msg, note=f"多次失败({fails}/{MAX_VIDEO_DOWNLOAD_FAILURES})")
        return
    vp["download_retry_until"] = time.time() + DOWNLOAD_RETRY_COOLDOWN
    _set_video(
        task,
        vi,
        status=VIDEO_PENDING,
        message=f"下载失败({fails}/{MAX_VIDEO_DOWNLOAD_FAILURES})，{DOWNLOAD_RETRY_COOLDOWN // 60} 分钟后重试",
    )
    _log(task, "warn", f"下载失败({fails}/{MAX_VIDEO_DOWNLOAD_FAILURES}) — {title}")
    _persist_tasks()


def _set_channel(task: TaskState, vi: int, ci: int, status: str, *, error: str = ""):
    if not _task_alive(task.task_id):
        return
    if vi < 0 or vi >= len(task.video_progress):
        return
    chs = task.video_progress[vi].get("channels", [])
    if ci < 0 or ci >= len(chs):
        return
    chs[ci]["status"] = status
    if status == CH_DONE:
        chs[ci]["sent_at"] = _now_str()
        chs[ci].pop("error", None)
    elif status == CH_FAILED and error:
        chs[ci]["error"] = error[:120]
    _persist_tasks()


def _channel_error_text(ch: dict) -> str:
    return (ch.get("error") or "").strip()


def _video_has_content_rejection(vp: dict) -> bool:
    if "10000" in (vp.get("message") or ""):
        return True
    return any("10000" in _channel_error_text(c) for c in vp.get("channels", []))


def _repair_fatal_post_videos(task: TaskState) -> int:
    """将内容被拒或全部频道失败但仍留在待发队列的视频移入 failed。"""
    fixed = 0
    for vp in task.video_progress:
        if vp.get("status") == VIDEO_FAILED:
            continue
        chs = vp.get("channels", [])
        if not chs:
            continue
        done_n = sum(1 for c in chs if c.get("status") == CH_DONE)
        failed_n = sum(1 for c in chs if c.get("status") == CH_FAILED)
        if done_n > 0:
            continue
        if failed_n == 0:
            continue

        if _video_has_content_rejection(vp):
            for c in chs:
                if c.get("status") in (CH_PENDING, CH_POSTING):
                    c["status"] = CH_FAILED
                    c["error"] = "内容被拒，已跳过"
            vp["status"] = VIDEO_FAILED
            vp["message"] = "内容被平台拒绝（错误码 10000）"
            fixed += 1
            continue

        if failed_n == len(chs):
            vp["status"] = VIDEO_FAILED
            vp["message"] = vp.get("message") or f"全部频道失败 ({failed_n})"
            fixed += 1
    if fixed:
        _persist_tasks()
    return fixed


def _repair_retryable_param_update_videos(task: TaskState) -> int:
    """参数更新后仍卡在 failed +「可重新发送」的视频，重置为 pending。"""
    fixed = 0
    for vp in task.video_progress:
        if vp.get("status") != VIDEO_FAILED:
            continue
        msg = vp.get("message") or ""
        if "可重新发送" not in msg and "参数已更新" not in msg:
            continue
        vp["status"] = VIDEO_PENDING
        vp["download_failures"] = 0
        vp["download_retry_until"] = 0
        for c in vp.get("channels") or []:
            if c.get("status") == CH_FAILED:
                c["status"] = CH_PENDING
                c.pop("error", None)
        fixed += 1
    return fixed


_ACCOUNT_TYPE_LABELS = {"qq": "QQ 主号", "bot": "Bot 账号"}


def _sanitize_account_types(account_types: list[str] | None) -> list[str]:
    """规范化账号类型：仅保留 qq / bot，去重保序。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in account_types or []:
        t = str(raw or "").strip().lower()
        if t not in ("qq", "bot") or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _account_types_from_ids(account_ids: list[str] | None) -> list[str]:
    """从旧版 account_ids 推导绑定的账号类型。"""
    types: list[str] = []
    for aid in account_ids or []:
        s = str(aid or "").strip()
        if s.startswith("qq:") and "qq" not in types:
            types.append("qq")
        elif s.startswith("bot:") and "bot" not in types:
            types.append("bot")
    return types


def _account_type_labels(account_types: list[str] | None) -> list[str]:
    return [_ACCOUNT_TYPE_LABELS.get(t, t) for t in _sanitize_account_types(account_types)]


def _account_ids_for_types(account_types: list[str] | None) -> list[str]:
    """按绑定类型展开为当前配置中的全部账号 ID（发送时随机选用）。"""
    wanted = set(_sanitize_account_types(account_types))
    if not wanted:
        return []
    return [a["id"] for a in list_accounts_public() if a.get("type") in wanted]


def _normalize_task_account_binding(payload: dict) -> tuple[list[str], list[str], list[str]]:
    """
    统一任务账号绑定为 account_types。
    兼容旧字段 account_ids：若未显式传类型，则从已选账号 ID 推导。
    返回 (account_types, account_ids_snapshot, account_names)。
    """
    types = _sanitize_account_types(payload.get("account_types"))
    if not types:
        types = _account_types_from_ids(payload.get("account_ids"))
    ids = _account_ids_for_types(types)
    names = _account_type_labels(types)
    return types, ids, names


def _is_collection_source(task: TaskState | dict) -> bool:
    payload = task.payload if isinstance(task, TaskState) else task
    return payload.get("source") == SOURCE_COLLECTION


def _task_name(
    keyword: str,
    platform: str,
    task_type: str,
    *,
    source: str = SOURCE_SEARCH,
    collection_label: str = "",
) -> str:
    if task_type == TASK_TYPE_CUSTOM or source == SOURCE_MANUAL:
        suffix = " · 长期" if task_type == TASK_TYPE_RECURRING else ""
        return f"手动链接 · 抖音{suffix}"
    if source == SOURCE_COLLECTION:
        label = (collection_label or "收藏夹").strip() or "收藏夹"
        suffix = " · 长期" if task_type == TASK_TYPE_RECURRING else ""
        return f"收藏夹 · {label}{suffix}"
    kw = keyword or "未命名"
    plat = "B站" if platform == "bili" else "抖音"
    suffix = " · 长期" if task_type == TASK_TYPE_RECURRING else ""
    return f"关键词 · {kw} · {plat}{suffix}"


def _sync_progress_channels(vp: dict, channels: list[dict]) -> dict:
    old_by_name = {c.get("name", ""): c for c in vp.get("channels", [])}
    new_prog = []
    for ch in channels:
        name = ch.get("name", "")
        prev = old_by_name.get(name)
        if prev and prev.get("status") == CH_DONE:
            new_prog.append(dict(prev))
        else:
            new_prog.append({"name": name, "status": CH_PENDING, "sent_at": ""})
    out = dict(vp)
    out["channels"] = new_prog
    if vp.get("status") not in (VIDEO_DONE, VIDEO_SKIPPED):
        if vp.get("status") in (VIDEO_WAITING, VIDEO_DOWNLOADING, VIDEO_POSTING):
            out["status"] = VIDEO_PENDING
            out["message"] = "参数已更新"
        elif vp.get("status") == VIDEO_FAILED:
            out["status"] = VIDEO_PENDING
            out["message"] = "参数已更新，可重新发送"
            out["download_failures"] = 0
            out["download_retry_until"] = 0
    return out


def _merge_task_videos_on_update(
    task: TaskState,
    selected_videos: list[dict],
    channels: list[dict],
) -> tuple[list[dict], list[dict]]:
    old_payload = {v.get("id"): v for v in task.payload.get("videos", []) if v.get("id")}
    old_progress = {v.get("id"): v for v in task.video_progress if v.get("id")}
    merged_videos: list[dict] = []
    merged_progress: list[dict] = []
    for v in selected_videos:
        vid = v.get("id", "")
        if not vid:
            continue
        merged_videos.append({**old_payload.get(vid, {}), **v})
        if vid in old_progress:
            vp = old_progress[vid]
            # done/skipped 保持终态；failed 允许随参数更新重置为可重试
            if vp.get("status") in (VIDEO_DONE, VIDEO_SKIPPED):
                merged_progress.append(vp)
            else:
                merged_progress.append(_sync_progress_channels(vp, channels))
        else:
            merged_progress.append(_init_video_progress([v], channels)[0])
    return merged_videos, merged_progress


def _tasks_by_name() -> list[TaskState]:
    return sorted(_tasks.values(), key=lambda t: (t.name or "").casefold())


def _task_summary(task: TaskState) -> dict:
    done = sum(1 for v in task.video_progress if v["status"] in (VIDEO_DONE, VIDEO_SKIPPED))
    payload = task.payload
    return {
        "task_id": task.task_id,
        "name": task.name,
        "status": task.status,
        "task_type": payload.get("task_type", TASK_TYPE_ONCE),
        "platform": task.platform,
        "keyword": task.keyword,
        "search_sort": payload.get("search_sort", "recent"),
        "video_count": task.video_count,
        "channel_count": task.channel_count,
        "schedule_cron": _get_task_cron(task),
        "schedule_desc": describe_cron(_get_task_cron(task)),
        "batch_count": payload.get("batch_count", 0),
        "source": payload.get("source", SOURCE_SEARCH),
        "douyin_cookie_index": int(payload.get("douyin_cookie_index", 0)),
        "douyin_collects_id": payload.get("douyin_collects_id", ""),
        "collection_account_label": payload.get("collection_account_label", ""),
        "include_topics": bool(payload.get("include_topics", True)),
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "result": task.result,
        "videos_done": done,
    }


def _task_detail(task: TaskState) -> dict:
    payload = task.payload
    types, ids, names = _normalize_task_account_binding(payload)
    return {
        **_task_summary(task),
        "channels": payload.get("channels", []),
        "videos": payload.get("videos", []),
        "account_types": types,
        "account_ids": ids,
        "account_names": names or payload.get("account_names", []),
        "search_sort": payload.get("search_sort", "recent"),
        "video_progress": task.video_progress,
        "logs": task.logs[-50:],
    }


def _persist_tasks():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "tasks": [
            {
                **_task_summary(t),
                "logs": t.logs[-200:],
                "payload": t.payload,
                "video_progress": t.video_progress,
            }
            for t in _tasks_by_name()
        ]
    }
    ACTIVE_TASKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ingest_task_item(item: dict) -> bool:
    migrated = False
    payload = item.get("payload", {})
    if not payload.get("schedule_cron"):
        interval = float(payload.get("video_interval_minutes", item.get("video_interval_minutes", 20)))
        if interval <= 0:
            cron = ""
        else:
            idx = len(_tasks)
            start = int(payload.get("schedule_start_minute", stagger_start_minute(idx, item.get("task_id", ""))))
            cron = cron_from_legacy_interval(start, interval)
        payload["schedule_cron"] = cron
        payload.pop("video_interval_minutes", None)
        payload.pop("schedule_start_minute", None)
        item["payload"] = payload
        item.pop("video_interval_minutes", None)
        item.pop("schedule_start_minute", None)
        migrated = True
    if "search_sort" not in payload:
        payload["search_sort"] = "recent"
        item["payload"] = payload
        migrated = True
    task = TaskState(
        task_id=item["task_id"],
        name=item.get("name", ""),
        status=item.get("status", STATUS_CREATED),
        platform=item.get("platform", ""),
        keyword=item.get("keyword", ""),
        video_count=item.get("video_count", 0),
        channel_count=item.get("channel_count", 0),
        schedule_cron=item.get("schedule_cron") or item.get("payload", {}).get("schedule_cron", ""),
        created_at=item.get("created_at", ""),
        started_at=item.get("started_at", ""),
        finished_at=item.get("finished_at", ""),
        logs=item.get("logs", []),
        result=item.get("result", {}),
        payload=item.get("payload", {}),
        video_progress=item.get("video_progress", []),
    )
    was_running = task.status == STATUS_RUNNING
    if was_running:
        task.finished_at = ""
    elif not task.video_progress and task.payload.get("videos"):
        task.video_progress = _init_video_progress(
            task.payload["videos"], task.payload.get("channels", [])
        )
    _tasks[task.task_id] = task
    _sync_task_schedule_fields(task)
    if _repair_proxy_skipped_videos(task):
        migrated = True
    if _recover_stale_downloading(task):
        migrated = True
    if _repair_retryable_param_update_videos(task):
        migrated = True
    types, ids, names = _normalize_task_account_binding(task.payload)
    old_types = list(task.payload.get("account_types") or [])
    old_aids = list(task.payload.get("account_ids") or [])
    old_names = list(task.payload.get("account_names") or [])
    if types != old_types or ids != old_aids or names != old_names:
        task.payload["account_types"] = types
        task.payload["account_ids"] = ids
        task.payload["account_names"] = names
        migrated = True
    return migrated


def _load_tasks_file(path: Path) -> bool:
    if not path.exists():
        return False
    migrated = False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("tasks", []):
            if _ingest_task_item(item):
                migrated = True
    except Exception:
        pass
    return migrated


def _load_tasks_from_disk():
    from backend.data.user_data import init_user_data

    init_user_data()
    if ACTIVE_TASKS_FILE.exists():
        _load_tasks_file(ACTIVE_TASKS_FILE)
        return
    if LEGACY_USER_TASKS_DIR.exists():
        for user_dir in sorted(LEGACY_USER_TASKS_DIR.iterdir()):
            if user_dir.is_dir() and (user_dir / "tasks.json").exists():
                _load_tasks_file(user_dir / "tasks.json")
                if _tasks:
                    _persist_tasks()
                    return
    if LEGACY_TASKS_FILE.exists():
        _load_tasks_file(LEGACY_TASKS_FILE)
        if _tasks:
            _persist_tasks()


def list_tasks() -> list[dict]:
    return [_task_summary(t) for t in _tasks_by_name()]


def get_task(task_id: str) -> TaskState | None:
    return _tasks.get(task_id)


def get_task_detail(task_id: str) -> dict | None:
    task = get_task(task_id)
    if not task:
        return None
    _repair_fatal_post_videos(task)
    return _task_detail(task)


def delete_task(task_id: str) -> bool:
    task = get_task(task_id)
    if not task:
        return True
    if task.status == STATUS_RUNNING:
        _cancelled.add(task_id)
    _paused.discard(task_id)
    del _tasks[task_id]
    _persist_tasks()
    return True


def pause_task(task_id: str) -> bool:
    task = get_task(task_id)
    if not task or task.status != STATUS_RUNNING:
        return False

    task.status = STATUS_PAUSED
    task.finished_at = ""
    for vp in task.video_progress:
        if vp.get("status") in (VIDEO_WAITING, VIDEO_DOWNLOADING, VIDEO_POSTING):
            vp["status"] = VIDEO_PENDING
            vp["message"] = "已暂停"
    task.logs.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": "warn",
        "message": "任务已暂停，点击继续执行可恢复",
    })
    if len(task.logs) > 500:
        task.logs = task.logs[-500:]
    _paused.add(task_id)
    _persist_tasks()
    return True


def create_task(
    payload: dict,
    name: str = "",
    keyword: str = "",
    auto_start: bool = False,
) -> str:
    task_id = str(uuid.uuid4())
    platform = payload.get("platform", "")
    videos = list(payload.get("videos", []))
    channels = payload.get("channels", [])
    payload = dict(payload)
    account_types, account_ids, account_names = _normalize_task_account_binding(payload)
    if not account_types:
        raise ValueError("请至少选择 QQ 或 Bot")
    if not account_ids:
        raise ValueError("所选账号类型下暂无可用账号")
    payload["account_types"] = account_types
    payload["account_ids"] = account_ids
    payload["account_names"] = account_names
    payload["videos"] = videos

    if not name:
        name = _task_name(
            keyword,
            platform,
            payload.get("task_type", TASK_TYPE_ONCE),
            source=payload.get("source", SOURCE_SEARCH),
            collection_label=payload.get("collection_account_label", ""),
        )

    payload = _apply_schedule_to_payload(dict(payload))

    task = TaskState(
        task_id=task_id,
        name=name,
        status=STATUS_CREATED,
        platform=platform,
        keyword=keyword,
        video_count=len(videos),
        channel_count=len(channels),
        schedule_cron=payload.get("schedule_cron", ""),
        created_at=_now_str(),
        payload=payload,
        video_progress=_init_video_progress(videos, channels),
    )
    _tasks[task_id] = task
    _persist_tasks()

    if auto_start:
        start_task(task_id)
    return task_id


def update_task(task_id: str, payload: dict, keyword: str = "") -> bool:
    task = get_task(task_id)
    if not task:
        return False
    if task.status == STATUS_RUNNING:
        raise ValueError("请先暂停任务后再修改")

    platform = payload.get("platform", "")
    task_type = payload.get("task_type", TASK_TYPE_ONCE)
    channels = payload.get("channels", [])
    videos = list(payload.get("videos", []))
    kw = (keyword or payload.get("keyword") or "").strip()

    account_types, account_ids, account_names = _normalize_task_account_binding(payload)

    if not videos and task_type != TASK_TYPE_CUSTOM:
        raise ValueError("没有待发送的视频")
    if not channels:
        raise ValueError("请至少选择一个频道")
    if not account_types:
        raise ValueError("请至少选择 QQ 或 Bot")
    if not account_ids:
        raise ValueError("所选账号类型下暂无可用账号")
    if task_type == TASK_TYPE_RECURRING and not kw and payload.get("source") != SOURCE_COLLECTION:
        raise ValueError("长期任务需要填写搜索关键词")

    if videos:
        merged_videos, merged_progress = _merge_task_videos_on_update(task, videos, channels)
        if not merged_videos and task_type != TASK_TYPE_CUSTOM:
            raise ValueError("没有待发送的视频")
    else:
        merged_videos = list(task.payload.get("videos", []))
        merged_progress = list(task.video_progress)

    batch_count = int(task.payload.get("batch_count", 0))
    if task_type == TASK_TYPE_RECURRING and batch_count < 1:
        batch_count = 1

    new_payload = {
        **task.payload,
        "task_type": task_type,
        "platform": platform,
        "keyword": kw,
        "videos": merged_videos,
        "channels": channels,
        "account_types": account_types,
        "account_ids": account_ids,
        "account_names": account_names,
        "schedule_cron": payload.get("schedule_cron", task.schedule_cron),
        "search_sort": payload.get("search_sort", task.payload.get("search_sort", "recent")),
        "batch_count": batch_count,
        "source": payload.get("source", task.payload.get("source", SOURCE_SEARCH)),
        "douyin_cookie_index": int(
            payload.get("douyin_cookie_index", task.payload.get("douyin_cookie_index", 0))
        ),
        "douyin_collects_id": payload.get(
            "douyin_collects_id", task.payload.get("douyin_collects_id", "")
        ),
        "collection_account_label": payload.get(
            "collection_account_label", task.payload.get("collection_account_label", "")
        ),
        "include_topics": bool(
            payload.get("include_topics", task.payload.get("include_topics", True))
        ),
    }
    new_payload = _apply_schedule_to_payload(new_payload)

    task.payload = new_payload
    task.platform = platform
    task.keyword = kw
    task.schedule_cron = new_payload.get("schedule_cron", "")
    task.video_count = len(merged_videos)
    task.channel_count = len(channels)
    task.video_progress = merged_progress
    task.name = _task_name(
        kw,
        platform,
        task_type,
        source=new_payload.get("source", SOURCE_SEARCH),
        collection_label=new_payload.get("collection_account_label", ""),
    )

    if task.status in (STATUS_DONE, STATUS_FAILED):
        if any(not _video_is_terminal(vp) for vp in merged_progress):
            task.status = STATUS_PAUSED if task.started_at else STATUS_CREATED
            task.finished_at = ""
            task.result = {}

    _log(task, "info", "任务参数已更新")
    _persist_tasks()
    return True


def append_custom_videos(task_id: str, text: str) -> dict:
    """自定义任务：从粘贴文本解析并追加抖音视频（运行中也可追加）。"""
    from backend.services.parse_douyin_links import parse_douyin_link_list

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    if task.payload.get("task_type") != TASK_TYPE_CUSTOM:
        raise ValueError("仅自定义任务支持追加链接")

    parsed = parse_douyin_link_list(text)
    existing = _existing_video_ids(task)
    fresh = [v for v in parsed.get("videos", []) if v.get("id") and v["id"] not in existing]
    channels = task.payload.get("channels", [])
    awaiting = False

    for v in fresh:
        task.payload.setdefault("videos", []).append(v)
        task.video_progress.append(_init_video_progress([v], channels)[0])

    if fresh:
        _trim_video_history(task)
        task.video_count = len(task.video_progress)
        awaiting = task.payload.pop("awaiting_videos", False)
        if task.status in (STATUS_DONE, STATUS_FAILED):
            if any(not _video_is_terminal(vp) for vp in task.video_progress):
                task.status = STATUS_PAUSED if task.started_at else STATUS_CREATED
                task.finished_at = ""
                task.result = {}

    dup = len(parsed.get("videos", [])) - len(fresh)
    if fresh:
        msg = f"追加 {len(fresh)} 条视频"
        if dup:
            msg += f"（跳过 {dup} 条重复）"
        _log(task, "info", msg)
    elif parsed.get("url_count"):
        _log(task, "warn", "未追加新视频（可能全部重复或解析失败）")
    _persist_tasks()

    if fresh and awaiting and task.status == STATUS_PAUSED:
        start_task(task_id)

    return {
        "added": len(fresh),
        "duplicate": dup,
        "errors": parsed.get("errors", []),
        "video_count": task.video_count,
    }


def _prepend_collection_videos(task: TaskState, fresh: list[dict]) -> None:
    """将同步到的新收藏插入列表头部（存储：新→旧）。"""
    channels = task.payload.get("channels", [])
    for v in reversed(fresh):
        task.payload.setdefault("videos", []).insert(0, v)
        task.video_progress.insert(0, _init_video_progress([v], channels)[0])


def sync_collection_videos(task_id: str) -> dict:
    """收藏夹任务：从最新收藏起同步，遇到任务中已有视频即停止（运行中也可同步）。"""
    from backend.services.douyin_collection import fetch_douyin_collection_new

    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")
    if not _is_collection_source(task):
        raise ValueError("仅收藏夹任务支持同步收藏")

    cookie_index = int(task.payload.get("douyin_cookie_index", 0))
    collects_id = str(task.payload.get("douyin_collects_id", "") or "")
    existing = _existing_video_ids(task)
    result = fetch_douyin_collection_new(
        cookie_index,
        existing,
        collects_id=collects_id,
        existing_newest_ids=_task_head_video_ids(task),
    )
    if result.get("error") and not result.get("videos"):
        err = result["error"]
        if result.get("pages_fetched", 0) > 1:
            _log(task, "warn", f"同步收藏翻页中断: {err}")
        else:
            raise ValueError(err)

    fresh = result.get("videos") or []
    should_resume = bool(
        fresh
        and task.started_at
        and (
            task.status == STATUS_PAUSED
            or (task.status == STATUS_RUNNING and task_id not in _active_loops)
        )
    )

    if fresh:
        _prepend_collection_videos(task, fresh)
        _trim_video_history(task)
        task.video_count = len(task.video_progress)
        task.payload.pop("awaiting_videos", None)
        if task.status in (STATUS_DONE, STATUS_FAILED):
            if any(not _video_is_terminal(vp) for vp in task.video_progress):
                task.status = STATUS_PAUSED if task.started_at else STATUS_CREATED
                task.finished_at = ""
                task.result = {}
        pages = result.get("pages_fetched", 0)
        partial = f"（部分页失败: {result['error']}）" if result.get("error") else ""
        stop_hint = "，已追上已有收藏" if result.get("stopped_on_duplicate") else ""
        _log(task, "info", f"同步收藏：新增 {len(fresh)} 条（扫描 {pages} 页）{stop_hint}{partial}")
    else:
        _log(task, "info", "同步收藏：暂无新视频")
    _persist_tasks()

    return {
        "added": len(fresh),
        "video_count": task.video_count,
        "pages_fetched": result.get("pages_fetched", 0),
        "stopped_on_duplicate": bool(result.get("stopped_on_duplicate")),
        "warning": result.get("error") or "",
        "should_resume": should_resume,
    }


def start_task(task_id: str) -> bool:
    task = get_task(task_id)
    if not task:
        return False
    if task.status == STATUS_RUNNING:
        if task_id in _active_loops:
            return False
        if not _task_has_send_work(task) and not (
            _is_recurring(task) and _all_videos_terminal(task)
        ):
            return False
        _paused.discard(task_id)
        asyncio.create_task(_run_batch_task(task_id))
        return True
    if task.status not in (STATUS_CREATED, STATUS_PAUSED):
        return False
    _paused.discard(task_id)
    task.status = STATUS_RUNNING
    _recover_stale_waits(task)
    for vp in task.video_progress:
        if vp.get("status") == VIDEO_WAITING:
            vp["status"] = VIDEO_PENDING
            vp["message"] = ""
            vp.pop("wait_until", None)
    _persist_tasks()
    asyncio.create_task(_run_batch_task(task_id))
    return True


async def _interruptible_sleep(seconds: int, task_id: str, task: TaskState | None = None) -> bool:
    """基于墙钟时间等待，Mac 休眠唤醒后会立即结束等待。"""
    if seconds <= 0:
        return _stop_reason(task_id) is None
    deadline = time.time() + seconds
    planned = seconds
    while True:
        if _stop_reason(task_id):
            return False
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(1.0, remaining))
    if task and time.time() - (deadline - planned) > planned + 60:
        _log(task, "info", "系统休眠结束，继续执行")
    return _stop_reason(task_id) is None


def _is_recurring(task: TaskState) -> bool:
    return task.payload.get("task_type") == TASK_TYPE_RECURRING


def _is_search_rate_limit_error(error: str) -> bool:
    text = (error or "").lower()
    return (
        "frequency is too high" in text
        or "frequency" in text and "too high" in text
        or "rate limit" in text
        or "请求过于频繁" in text
        or "频率" in text and "过高" in text
    )


def _seconds_until_next_midnight() -> int:
    now = datetime.now()
    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(1, int((next_midnight - now).total_seconds()))


def _is_daily_search_quota_exhausted(error: str) -> bool:
    if not _is_search_rate_limit_error(error):
        return False
    match = _SEARCH_COOLDOWN_MINUTES_RE.search(error or "")
    return bool(match and int(match.group(1)) >= 60)


def _parse_search_cooldown_seconds(error: str) -> int | None:
    """从 GUAIKEI 限流文案解析建议等待秒数；无法解析时返回 None。"""
    if not _is_search_rate_limit_error(error):
        return None
    match = _SEARCH_COOLDOWN_MINUTES_RE.search(error or "")
    if match:
        minutes = int(match.group(1))
        if minutes >= 60:
            return _seconds_until_next_midnight()
        return minutes * 60 + 120
    return 30 * 60


def _format_pause_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours} 小时" + (f" {minutes} 分" if minutes else "")
    if seconds >= 60:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _old_ratio_pause_seconds(ratio: float) -> int | None:
    if ratio > OLD_RATIO_PAUSE_24H:
        return PAUSE_24H_SECONDS
    if ratio > OLD_RATIO_PAUSE_12H:
        return PAUSE_12H_SECONDS
    return None


def _apply_recurring_pause(task: TaskState, seconds: int, reason: str) -> float:
    until = _apply_task_pause(task, "fetch_pause_until", seconds, reason)
    _apply_task_pause(task, "post_pause_until", seconds, reason)
    return until


def _apply_task_pause(task: TaskState, key: str, seconds: int, reason: str) -> float:
    until = time.time() + max(0, int(seconds))
    task.payload[key] = until
    task.payload[f"{key}_reason"] = reason
    _persist_tasks()
    return until


def _clear_task_pause(task: TaskState, key: str) -> None:
    task.payload.pop(key, None)
    task.payload.pop(f"{key}_reason", None)


def _active_pause_until(task: TaskState, key: str) -> float:
    return float(task.payload.get(key) or 0)


def _in_recurring_pause(task: TaskState) -> bool:
    now = time.time()
    return (
        _active_pause_until(task, "fetch_pause_until") > now
        or _active_pause_until(task, "post_pause_until") > now
    )


async def _wait_task_pause(
    task_id: str,
    task: TaskState,
    key: str,
    label: str,
) -> bool:
    until = _active_pause_until(task, key)
    now = time.time()
    if until <= now:
        if until > 0:
            _clear_task_pause(task, key)
            _persist_tasks()
        return True
    wait_sec = int(until - now)
    _log(task, "info", f"{label}中，约 {_format_pause_duration(wait_sec)} 后继续...")
    ok = await _interruptible_sleep(wait_sec, task_id, task)
    if ok:
        _clear_task_pause(task, key)
        _persist_tasks()
    return ok


def _append_fetched_videos(task: TaskState, fresh: list[dict], channels: list[dict]) -> None:
    payload = task.payload
    if _is_collection_source(task):
        _prepend_collection_videos(task, fresh)
    elif sends_newest_last(
        source=payload.get("source", SOURCE_SEARCH),
        search_sort=payload.get("search_sort", "recent"),
    ):
        for v in reversed(fresh):
            payload["videos"].insert(0, v)
            task.video_progress.insert(0, _init_video_progress([v], channels)[0])
    else:
        for v in fresh:
            payload["videos"].append(v)
            task.video_progress.append(_init_video_progress([v], channels)[0])


def _all_videos_terminal(task: TaskState) -> bool:
    if not task.video_progress:
        return False
    return all(
        v.get("status") in (VIDEO_DONE, VIDEO_SKIPPED, VIDEO_FAILED)
        for v in task.video_progress
    )


def _task_has_send_work(task: TaskState) -> bool:
    """是否还有待自动发送/续发的视频。"""
    for vp in task.video_progress:
        st = vp.get("status")
        if st in (VIDEO_PENDING, VIDEO_WAITING, VIDEO_DOWNLOADING):
            return True
        if st == VIDEO_POSTING and not _video_all_channels_done(vp):
            return True
    return False


def _collection_task_idle(task: TaskState) -> bool:
    """收藏夹长期任务：本批已发完且暂无待发送项。"""
    return (
        _is_recurring(task)
        and _is_collection_source(task)
        and not _task_has_send_work(task)
    )


def _should_kick_task_loop(task_id: str, task: TaskState) -> bool:
    """运行中但协程已退出，且仍有工作可做。"""
    if task.status != STATUS_RUNNING or task_id in _active_loops:
        return False
    if _collection_task_idle(task):
        return False
    return _task_has_send_work(task) or (
        _is_recurring(task)
        and _all_videos_terminal(task)
        and not _is_collection_source(task)
        and not _in_recurring_pause(task)
    )


def _existing_video_ids(task: TaskState) -> set[str]:
    return {vid for v in task.video_progress if (vid := v.get("id"))}


def _task_head_video_ids(task: TaskState, limit: int = 20) -> list[str]:
    """任务列表头部为最新条目（存储顺序：新→旧）。"""
    ids: list[str] = []
    for v in task.payload.get("videos", [])[:limit]:
        vid = v.get("id")
        if vid:
            ids.append(vid)
    return ids


def _trim_video_history(task: TaskState, max_count: int = MAX_TASK_VIDEOS):
    videos = task.payload.get("videos", [])
    if len(videos) <= max_count:
        return
    drop = len(videos) - max_count
    task.payload["videos"] = videos[:max_count]
    task.video_progress = task.video_progress[:max_count]
    task.video_count = len(task.video_progress)
    _persist_tasks()
    _log(task, "info", f"视频列表已满，保留最新 {max_count} 条")


async def _fetch_and_append_batch(task_id: str, task: TaskState) -> FetchBatchResult:
    if not _task_alive(task_id):
        return FetchBatchResult()
    payload = task.payload
    existing = _existing_video_ids(task)

    if _is_collection_source(task):
        from backend.services.douyin_collection import fetch_douyin_collection_new

        cookie_index = int(payload.get("douyin_cookie_index", 0))
        collects_id = str(payload.get("douyin_collects_id", "") or "")
        newest_ids = _task_head_video_ids(task)

        def _pull():
            return fetch_douyin_collection_new(
                cookie_index,
                existing,
                collects_id=collects_id,
                existing_newest_ids=newest_ids,
            )

        result = await asyncio.to_thread(_pull)
        if result.get("error") and not result.get("videos"):
            _log(task, "warn", f"同步收藏失败: {result['error']}")
            return FetchBatchResult()
        fresh = result.get("videos") or []
        if not fresh:
            _log(task, "warn", "未找到新收藏视频")
            return FetchBatchResult()
    else:
        platform = payload.get("platform", "")
        keyword = (payload.get("keyword") or task.keyword or "").strip()
        if not keyword:
            _log(task, "error", "长期任务缺少搜索关键词")
            return FetchBatchResult()

        search_sort = payload.get("search_sort", "recent")
        bili_pages = max(1, min(int(payload.get("bili_pages") or 1), 10))
        search_result = await asyncio.to_thread(
            search_videos, platform, keyword, bili_pages, search_sort
        )
        if search_result.get("error") and not search_result.get("videos"):
            err = str(search_result["error"])
            _log(task, "warn", f"搜索失败: {err}")
            pause_sec = _parse_search_cooldown_seconds(err) or SEARCH_ERROR_PAUSE_SECONDS
            reason = "api_rate_limit" if _is_search_rate_limit_error(err) else "search_error"
            until = _apply_recurring_pause(task, pause_sec, reason)
            if _is_daily_search_quota_exhausted(err):
                pause_msg = "搜索额度已耗尽，暂停至次日 0 点后再试"
            else:
                pause_msg = f"搜索暂停 {_format_pause_duration(pause_sec)} 后再试"
            _log(task, "warn", pause_msg)
            return FetchBatchResult(added=0, pause_until=until, pause_reason=reason)

        warning = search_result.get("warning")
        if warning:
            _log(task, "info", str(warning))

        all_videos = search_result.get("videos", []) or []
        raw_count = search_result.get("raw_count")
        if raw_count is None:
            raw_count = len(all_videos)
        filtered = search_result.get("filtered_count", 0)

        if raw_count == 0 and not all_videos:
            until = _apply_recurring_pause(
                task,
                PAUSE_24H_SECONDS,
                "fetch_empty",
            )
            _log(
                task,
                "warn",
                f"未拉到任何视频，{_format_pause_duration(PAUSE_24H_SECONDS)} 后再搜索",
            )
            return FetchBatchResult(added=0, pause_until=until, pause_reason="fetch_empty")

        fresh = [v for v in all_videos if v.get("id") not in existing]
        total = len(all_videos)

        if total == 0:
            until = _apply_recurring_pause(task, PAUSE_24H_SECONDS, "no_new")
            _log(
                task,
                "warn",
                f"未找到新视频（拉取 {raw_count} 条，过滤 {filtered} 条），"
                f"{_format_pause_duration(PAUSE_24H_SECONDS)} 后再搜索",
            )
            return FetchBatchResult(added=0, pause_until=until, pause_reason="no_new")

        dup_count = total - len(fresh)
        old_ratio = dup_count / total
        pause_seconds = _old_ratio_pause_seconds(old_ratio)

        if pause_seconds:
            until = _apply_recurring_pause(task, pause_seconds, "old_ratio")
            pause_label = _format_pause_duration(pause_seconds)
            if not fresh:
                _log(
                    task,
                    "warn",
                    f"旧视频占比 {old_ratio:.0%}（{dup_count}/{total}），无新视频，"
                    f"暂停 {pause_label} 后再搜索",
                )
                return FetchBatchResult(added=0, pause_until=until, pause_reason="old_ratio")
            _log(
                task,
                "warn",
                f"旧视频占比 {old_ratio:.0%}（{dup_count}/{total}），"
                f"新增 {len(fresh)} 条，暂停 {pause_label} 后再发帖/搜索",
            )

    channels = payload.get("channels", [])
    _append_fetched_videos(task, fresh, channels)

    _trim_video_history(task)
    payload["batch_count"] = int(payload.get("batch_count", 0)) + 1
    task.video_count = len(task.video_progress)
    _persist_tasks()
    _log(
        task, "info",
        f"第 {payload['batch_count']} 批：新增 {len(fresh)} 条视频（累计 {task.video_count} 条）",
    )
    pause_until = max(
        _active_pause_until(task, "post_pause_until"),
        _active_pause_until(task, "fetch_pause_until"),
    )
    reason = str(task.payload.get("post_pause_until_reason") or task.payload.get("fetch_pause_until_reason") or "")
    return FetchBatchResult(added=len(fresh), pause_until=pause_until, pause_reason=reason)


def _finalize_once_task(task: TaskState, all_posted: list[str], all_failed: list[str]):
    videos_done = sum(1 for v in task.video_progress if v.get("status") == VIDEO_DONE)
    videos_skipped = sum(1 for v in task.video_progress if v.get("status") == VIDEO_SKIPPED)
    task.result = {
        "videos_total": len(task.video_progress),
        "videos_done": videos_done,
        "videos_skipped": videos_skipped,
        "posted": all_posted,
        "failed": all_failed,
    }
    task.status = STATUS_DONE if videos_done > 0 else STATUS_FAILED
    task.finished_at = _now_str()
    _persist_tasks()
    _log(task, "info", f"全部完成：发送 {videos_done} 条，跳过 {videos_skipped} 条")


async def _run_video_pass(
    task_id: str,
    task: TaskState,
    all_posted: list[str],
    all_failed: list[str],
) -> bool:
    payload = task.payload
    videos = payload.get("videos", [])
    total_videos = len(videos)
    send_indices = _task_send_indices(task)

    for vi in send_indices:
        if vi >= len(videos):
            continue
        if _aborted(task_id):
            return True

        vp = task.video_progress[vi]
        if vp.get("status") == VIDEO_DOWNLOADING and not _is_stale_downloading(vp):
            continue
        if _video_auto_skip(vp):
            continue
        if _video_download_cooldown(vp):
            continue
        if _video_all_channels_done(vp):
            _set_video(task, vi, status=VIDEO_DONE, message=vp.get("message") or "全部成功")
            continue

        if _needs_cron_wait(task, vi, vp):
            if not await _wait_for_cron_slot(task_id, task, vi):
                return True
            vp = task.video_progress[vi]
            if vp.get("status") == VIDEO_WAITING:
                _set_video(task, vi, status=VIDEO_PENDING, message="继续执行")

        if _aborted(task_id):
            return True

        posted, failed, stop_all = await _process_one_video(task_id, task, vi)
        all_posted.extend(posted)
        all_failed.extend(failed)

        if stop_all:
            if task.status == STATUS_PAUSED:
                _log(task, "error", "下载代理错误，任务已暂停，请检查网络后点击「继续运行」")
                return True
            _log(task, "error", "全局限流，终止剩余视频")
            try:
                pos = send_indices.index(vi)
            except ValueError:
                pos = -1
            for rest in send_indices[pos + 1:]:
                if not _aborted(task_id):
                    _set_video(task, rest, status=VIDEO_SKIPPED, message="因全局限流终止")
            return True

        vp = task.video_progress[vi]
        if not _video_auto_skip(vp) and not _video_all_channels_done(vp):
            _log(task, "warn", f"第 {vi + 1} 条未发完，等待下轮继续")
            return False

    return False


async def _run_batch_task(task_id: str):
    task = _tasks.get(task_id)
    if not task or task.status not in (STATUS_RUNNING, STATUS_CREATED, STATUS_PAUSED):
        return

    lock = _get_task_run_lock(task_id)
    if lock.locked():
        return

    async with lock:
        _active_loops.add(task_id)
        try:
            await _run_batch_task_inner(task_id, task)
        finally:
            _active_loops.discard(task_id)
            _cancelled.discard(task_id)
            was_paused = task_id in _paused
            _paused.discard(task_id)
            if was_paused and task_id in _tasks and _tasks[task_id].status == STATUS_RUNNING:
                _apply_pause(_tasks[task_id])


async def _run_batch_task_inner(task_id: str, task: TaskState):
    if _aborted(task_id) or task.status == STATUS_PAUSED:
        return

    payload = task.payload
    task.status = STATUS_RUNNING
    if not task.started_at:
        task.started_at = _now_str()
    task.finished_at = ""
    _ensure_video_progress(task)
    _persist_tasks()

    account_types, account_ids, account_names = _normalize_task_account_binding(payload)
    task.payload["account_types"] = account_types
    task.payload["account_ids"] = account_ids
    task.payload["account_names"] = account_names
    channels = payload.get("channels", [])
    if not account_types:
        _log(task, "error", "未选择 QQ 或 Bot")
        task.status = STATUS_FAILED
        task.finished_at = _now_str()
        _persist_tasks()
        return
    if not account_ids:
        _log(task, "error", "所选账号类型下暂无可用账号")
        task.status = STATUS_FAILED
        task.finished_at = _now_str()
        _persist_tasks()
        return
    if not channels:
        _log(task, "error", "未选择任何频道")
        task.status = STATUS_FAILED
        task.finished_at = _now_str()
        _persist_tasks()
        return

    recurring = _is_recurring(task)
    task_type = payload.get("task_type", TASK_TYPE_ONCE)
    if not payload.get("videos"):
        if task_type == TASK_TYPE_CUSTOM:
            _log(task, "info", "暂无视频，请通过「追加链接」添加后继续")
            task.payload["awaiting_videos"] = True
            task.status = STATUS_PAUSED
            _persist_tasks()
            return
        _log(task, "error", "没有待发送的视频")
        task.status = STATUS_FAILED
        task.finished_at = _now_str()
        _persist_tasks()
        return

    total_videos = len(payload.get("videos", []))
    already_done = sum(1 for v in task.video_progress if _video_is_terminal(v))
    if already_done:
        _log(task, "info", f"继续任务：已完成 {already_done}/{total_videos}，从断点续发")
    if recurring:
        _log(task, "info", f"长期任务启动：{task.keyword}，{total_videos} 个视频 → {len(channels)} 个频道")
    else:
        _log(task, "info", f"任务启动：{total_videos} 个视频 → {len(channels)} 个频道")

    all_posted: list[str] = []
    all_failed: list[str] = []

    while True:
        if _aborted(task_id):
            return

        if recurring:
            if not await _wait_task_pause(task_id, task, "post_pause_until", "发帖冷却"):
                return

        stop_all = await _run_video_pass(task_id, task, all_posted, all_failed)
        if _aborted(task_id):
            return
        if stop_all:
            if task.status == STATUS_PAUSED:
                _persist_tasks()
                return
            task.status = STATUS_FAILED
            task.finished_at = _now_str()
            _persist_tasks()
            return

        if not recurring:
            if _all_videos_terminal(task):
                _finalize_once_task(task, all_posted, all_failed)
            return

        if not _all_videos_terminal(task):
            return

        sync_label = "同步收藏" if _is_collection_source(task) else "搜索下一批"
        _log(task, "info", f"本批发送完毕，准备{sync_label}...")
        task.finished_at = ""
        _persist_tasks()

        if not await _interruptible_sleep(30, task_id):
            return

        if _is_collection_source(task):
            fetch_result = await _fetch_and_append_batch(task_id, task)
            if fetch_result.added <= 0:
                _log(
                    task,
                    "info",
                    "收藏夹暂无新视频，任务待机（有新收藏时点击「同步收藏」）",
                )
            return

        fetch_result = FetchBatchResult()
        while not _aborted(task_id):
            if not await _wait_task_pause(task_id, task, "fetch_pause_until", "拉取冷却"):
                return
            fetch_result = await _fetch_and_append_batch(task_id, task)
            if fetch_result.added > 0:
                break
            if fetch_result.pause_until > time.time():
                continue
            break

        if fetch_result.added <= 0:
            return


async def _permission_alert_with_auto_join(
    account_id: str,
    ch: dict,
    detail: str,
    task_id: str,
) -> None:
    """10023：后台尝试加频道，完成后再写入告警（不阻塞发帖主流程）。"""
    try:
        join_result = await asyncio.to_thread(
            join_guild_for_account,
            account_id,
            str(ch.get("guild_id") or ""),
        )
        join_msg = format_auto_join_message(join_result)
    except Exception as exc:
        join_msg = f"自动加频道失败：{str(exc)[:80]}"
    parts = [p for p in (detail, join_msg) if p]
    message = " | ".join(parts)
    try:
        append_alert(
            error_code=10023,
            account_id=account_id,
            account_label=account_label(account_id),
            guild_id=str(ch.get("guild_id") or ""),
            channel_id=str(ch.get("channel_id") or ""),
            channel_name=str(ch.get("name") or ""),
            message=message,
            task_id=task_id,
        )
    except Exception:
        pass


def _record_account_alert(
    err_type: str,
    account_id: str,
    ch: dict,
    detail: str,
    task_id: str,
) -> None:
    code = ALERT_ERR_CODES.get(err_type)
    if not code:
        return
    if err_type == "permission":
        asyncio.create_task(
            _permission_alert_with_auto_join(account_id, ch, detail, task_id)
        )
        return
    try:
        append_alert(
            error_code=code,
            account_id=account_id,
            account_label=account_label(account_id),
            guild_id=str(ch.get("guild_id") or ""),
            channel_id=str(ch.get("channel_id") or ""),
            channel_name=str(ch.get("name") or ""),
            message=detail,
            task_id=task_id,
        )
    except Exception:
        pass


def _format_ms(ms: int) -> str:
    if ms >= 60_000:
        return f"{ms // 1000}s"
    return f"{ms}ms"


async def _post_to_channels(
    task: TaskState,
    vi: int,
    channels: list[dict],
    output_path: str,
    title: str,
    account_ids: list[str],
) -> tuple[list[str], list[str], bool]:
    posted: list[str] = []
    failed: list[str] = []
    total = len(channels)
    skipped = 0
    post_times: list[int] = []
    session_t0 = time.perf_counter()

    try:
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
    except OSError:
        size_mb = 0.0

    title_short = (title or "")[:40]
    _log(
        task,
        "info",
        f"📮 发帖开始 · {title_short} · {size_mb:.1f}MB · {total} 频道",
    )

    for i, ch in enumerate(channels):
        if _aborted(task.task_id):
            return posted, failed, True

        guild_id = ch["guild_id"]
        channel_id = ch["channel_id"]
        ch_name = ch.get("name", f"{guild_id}/{channel_id}")
        seq = f"[{i + 1}/{total}]"

        ch_prog = task.video_progress[vi].get("channels", [])
        if i < len(ch_prog) and ch_prog[i].get("status") == CH_DONE:
            posted.append(ch_name)
            skipped += 1
            _log(task, "info", f"⏭ {seq} {ch_name} 已发过，跳过")
            continue

        account_id = _pick_post_account(account_ids)
        _set_channel(task, vi, i, CH_POSTING)
        _log(task, "info", f"📤 {seq} {ch_name}（{account_label(account_id)}）")

        include_topics = bool(task.payload.get("include_topics", True))
        t0 = time.perf_counter()
        ok, err_type, err_detail = await asyncio.to_thread(
            publish_video,
            guild_id,
            channel_id,
            output_path,
            title,
            account_id,
            include_topics=include_topics,
        )
        post_ms = int((time.perf_counter() - t0) * 1000)
        post_times.append(post_ms)

        channel_ok = False
        if ok:
            posted.append(ch_name)
            _set_channel(task, vi, i, CH_DONE)
            channel_ok = True
            _log(task, "success", f"✅ {seq} {ch_name} · {_format_ms(post_ms)}")
            if post_ms >= POST_SLOW_WARN_MS:
                _log(task, "warn", f"🐢 上传偏慢 · {_format_ms(post_ms)} · {size_mb:.1f}MB")
        elif err_type in RETRYABLE_ERR_TYPES:
            if err_type in ALERT_ERR_CODES:
                _record_account_alert(err_type, account_id, ch, err_detail, task.task_id)
            reason = RETRY_REASON_LABEL.get(err_type, err_type)
            _log(
                task,
                "warn",
                f"⚠️ {seq} {ch_name} {reason} · {_format_ms(post_ms)}，换号重试…",
            )
            retry_ok = False
            err_detail2 = err_detail
            lo, hi = POST_RETRY_SLEEP.get(err_type, (12, 20))
            tried_accounts = {account_id}
            prefer_qq = err_type == "bot_abnormal"
            for attempt in range(1, 4):
                if _aborted(task.task_id):
                    return posted, failed, True
                retry_wait = random.randint(lo, hi)
                _log(task, "info", f"⏸ 换号等待 {retry_wait}s（{reason}）")
                if not await _interruptible_sleep(retry_wait, task.task_id):
                    return posted, failed, True
                retry_account = _pick_post_account(
                    account_ids, tried_accounts, prefer_qq=prefer_qq,
                )
                tried_accounts.add(retry_account)
                retry_label = account_label(retry_account)
                t1 = time.perf_counter()
                ok2, err_type2, err_detail2 = await asyncio.to_thread(
                    publish_video,
                    guild_id,
                    channel_id,
                    output_path,
                    title,
                    retry_account,
                    include_topics=include_topics,
                )
                retry_ms = int((time.perf_counter() - t1) * 1000)
                post_times.append(retry_ms)
                if ok2:
                    posted.append(ch_name)
                    _set_channel(task, vi, i, CH_DONE)
                    channel_ok = True
                    _log(
                        task,
                        "success",
                        f"✅ {seq} {ch_name} 第{attempt}次换号成功（{retry_label} · {_format_ms(retry_ms)}）",
                    )
                    retry_ok = True
                    break
                if err_type2 in ALERT_ERR_CODES:
                    _record_account_alert(err_type2, retry_account, ch, err_detail2, task.task_id)
                if err_type2 == "bot_abnormal":
                    prefer_qq = True
                _log(
                    task,
                    "warn",
                    f"⚠️ {seq} {ch_name} 换号{attempt}仍失败 · {_format_ms(retry_ms)}",
                )
                account_id = retry_account
            if not retry_ok:
                failed.append(ch_name)
                _set_channel(task, vi, i, CH_FAILED, error=err_detail2 or f"{reason}换号失败")
                _log(task, "error", f"❌ {seq} {ch_name} 换号3次仍失败 · {_format_ms(post_ms)}起")
        elif err_type == "content_rejected":
            failed.append(ch_name)
            _set_channel(task, vi, i, CH_FAILED, error=err_detail)
            _log(
                task,
                "error",
                f"❌ {seq} {ch_name} 内容被拒绝 · {_format_ms(post_ms)}：{err_detail[:100]}",
            )
            for j in range(i + 1, len(channels)):
                if j >= len(ch_prog) or ch_prog[j].get("status") in (CH_DONE, CH_FAILED):
                    continue
                rest_name = channels[j].get("name", f"频道{j + 1}")
                failed.append(rest_name)
                _set_channel(task, vi, j, CH_FAILED, error="内容被拒，已跳过")
            _set_video(task, vi, status=VIDEO_FAILED, message="内容被平台拒绝（错误码 10000）")
            break
        elif err_type == "oidb_limit":
            failed.append(ch_name)
            _set_channel(task, vi, i, CH_FAILED, error=err_detail)
            _log(task, "error", f"❌ {seq} {ch_name} 全局OIDB限流 · {_format_ms(post_ms)}")
            return posted, failed, True
        else:
            failed.append(ch_name)
            _set_channel(task, vi, i, CH_FAILED, error=err_detail or err_type or "未知错误")
            detail = err_detail or err_type or "未知错误"
            _log(task, "error", f"❌ {seq} {ch_name} 发帖失败 · {_format_ms(post_ms)}：{detail[:100]}")

        # 发送成功后再发下一频道，间隔 1~3 分钟
        if channel_ok and i < len(channels) - 1:
            wait_sec = random.randint(CHANNEL_INTERVAL_MIN, CHANNEL_INTERVAL_MAX)
            _log(task, "info", f"⏸ 频道间隔 {wait_sec // 60}分{wait_sec % 60:02d}秒")
            if not await _interruptible_sleep(wait_sec, task.task_id):
                return posted, failed, True

    session_ms = int((time.perf_counter() - session_t0) * 1000)
    ok_count = len(posted)
    fail_count = len(failed)
    avg_ms = sum(post_times) // len(post_times) if post_times else 0
    level = "info" if fail_count == 0 else "warn"
    _log(
        task,
        level,
        f"🏁 发帖结束 · 成功 {ok_count}/{total}"
        f"（跳过 {skipped}，失败 {fail_count}）"
        f" · 会话 {_format_ms(session_ms)} · 均帖 {_format_ms(avg_ms)}",
    )
    return posted, failed, False


def _get_task_run_lock(task_id: str) -> asyncio.Lock:
    if task_id not in _task_run_locks:
        _task_run_locks[task_id] = asyncio.Lock()
    return _task_run_locks[task_id]


def _reset_stale_channel_posting(vp: dict):
    for ch in vp.get("channels", []):
        if ch.get("status") == CH_POSTING:
            ch["status"] = CH_PENDING


def _get_video_process_lock(task_id: str, vi: int) -> asyncio.Lock:
    key = (task_id, vi)
    if key not in _video_process_locks:
        _video_process_locks[key] = asyncio.Lock()
    return _video_process_locks[key]


def can_manual_send_video(task: TaskState, vi: int) -> bool:
    if vi < 0 or vi >= len(task.video_progress):
        return False
    vp = task.video_progress[vi]
    st = vp.get("status")
    if st == VIDEO_DOWNLOADING:
        return _is_stale_downloading(vp)
    if st == VIDEO_DONE:
        return False
    if st == VIDEO_POSTING and not _video_all_channels_done(vp):
        return True
    if st == VIDEO_POSTING:
        return False
    return True


def _find_video_index(task: TaskState, video_id: str) -> int:
    for i, v in enumerate(task.video_progress):
        if v.get("id") == video_id:
            return i
    for i, v in enumerate(task.payload.get("videos", [])):
        if v.get("id") == video_id:
            return i
    return -1


def _prepare_video_manual_send(task: TaskState, vi: int):
    vp = task.video_progress[vi]
    st = vp.get("status")
    if st == VIDEO_DOWNLOADING:
        _reset_stale_downloading_item(task, vi)
    if st in (VIDEO_SKIPPED, VIDEO_FAILED):
        vp["status"] = VIDEO_PENDING
        vp["account"] = ""
        vp["message"] = "手动重试"
        vp["started_at"] = ""
        vp["sent_at"] = ""
        vp["download_failures"] = 0
        vp["download_retry_until"] = 0
        vp.pop("wait_until", None)
        for ch in vp.get("channels", []):
            ch["status"] = CH_PENDING
            ch["sent_at"] = ""
    elif st == VIDEO_DONE:
        for ch in vp.get("channels", []):
            if ch.get("status") != CH_DONE:
                ch["status"] = CH_PENDING
                ch["sent_at"] = ""
        vp["status"] = VIDEO_PENDING
        vp["message"] = "手动重试"
    elif st == VIDEO_WAITING:
        vp["status"] = VIDEO_PENDING
        vp["message"] = ""
        vp.pop("wait_until", None)
    elif st == VIDEO_POSTING and not _video_all_channels_done(vp):
        _reset_stale_channel_posting(vp)
        vp["message"] = "手动续发"
    _persist_tasks()


def _recompute_task_completion(task: TaskState):
    if task.status == STATUS_RUNNING:
        return
    total = len(task.video_progress)
    if not total:
        return
    videos_done = sum(1 for v in task.video_progress if v.get("status") == VIDEO_DONE)
    videos_skipped = sum(1 for v in task.video_progress if v.get("status") == VIDEO_SKIPPED)
    videos_failed = sum(1 for v in task.video_progress if v.get("status") == VIDEO_FAILED)
    remaining = sum(
        1 for v in task.video_progress
        if v.get("status") not in (VIDEO_DONE, VIDEO_SKIPPED, VIDEO_FAILED)
    )
    task.result = {
        **task.result,
        "videos_total": total,
        "videos_done": videos_done,
        "videos_skipped": videos_skipped + videos_failed,
    }
    if remaining == 0:
        task.status = STATUS_DONE if videos_done > 0 else STATUS_FAILED
        task.finished_at = _now_str()
    elif task.status in (STATUS_DONE, STATUS_FAILED):
        task.status = STATUS_PAUSED
        task.finished_at = ""
    _persist_tasks()


async def send_task_video(task_id: str, video_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise ValueError("任务不存在")

    vi = _find_video_index(task, video_id)
    if vi < 0:
        raise ValueError("视频不存在")
    if not can_manual_send_video(task, vi):
        raise ValueError("该视频当前无法发送")

    lock = _get_video_process_lock(task_id, vi)
    if lock.locked():
        raise ValueError("该视频正在处理中，请稍候")

    async with lock:
        _ensure_video_progress(task)
        _prepare_video_manual_send(task, vi)
        video = task.payload["videos"][vi]
        _log(task, "info", f"手动发送: {video.get('title', '')[:50]}")
        posted, failed, stop_all = await _process_one_video(task_id, task, vi, skip_lock=True, manual_retry=True)
        if stop_all and not _aborted(task_id):
            _log(task, "error", "全局限流，手动发送终止")
        vp_after = task.video_progress[vi]
        if not posted and vp_after.get("status") in (VIDEO_FAILED, VIDEO_SKIPPED):
            raise ValueError(vp_after.get("message") or "发送失败，请查看日志")
        _recompute_task_completion(task)
        return {
            "ok": True,
            "posted": len(posted),
            "video_progress": task.video_progress[vi],
            "task_status": task.status,
        }


async def _process_one_video(
    task_id: str, task: TaskState, vi: int, *, skip_lock: bool = False, manual_retry: bool = False
) -> tuple[list[str], list[str], bool]:
    """下载并发送单条视频，返回 (posted, failed, stop_all)"""
    payload = task.payload
    platform = payload["platform"]
    videos = payload["videos"]
    channels = payload["channels"]
    # 每次发送前按类型重新展开，新增的 QQ/Bot 会自动进入随机池
    _, account_ids, _ = _normalize_task_account_binding(payload)
    if not account_ids:
        _log(task, "error", "所选账号类型下暂无可用账号")
        return [], [], True

    if vi >= len(videos):
        return [], [], False

    async def _run() -> tuple[list[str], list[str], bool]:
        vp = task.video_progress[vi]
        if _reset_stale_downloading_item(task, vi):
            vp = task.video_progress[vi]
        if not manual_retry and _video_auto_skip(vp):
            return [], [], False
        if not manual_retry and _video_download_cooldown(vp):
            return [], [], False
        if _video_is_terminal(vp):
            return [], [], False
        if _video_all_channels_done(vp):
            _set_video(task, vi, status=VIDEO_DONE, message=vp.get("message") or "全部成功")
            return [], [], False

        video = videos[vi]
        title = video.get("title", "")
        video_id = video.get("id", "")
        total_videos = len(videos)
        seq = send_sequence_pos(_task_send_indices(task), vi)
        partial_post = vp.get("status") == VIDEO_POSTING and not _video_all_channels_done(vp)

        if vp.get("status") == VIDEO_WAITING:
            _set_video(task, vi, status=VIDEO_PENDING, message="继续执行")

        if partial_post:
            _reset_stale_channel_posting(vp)
            done = sum(1 for c in vp.get("channels", []) if c.get("status") == CH_DONE)
            _log(task, "info", f"[{seq}/{total_videos}] 续发: {title[:50]}（已完成 {done}/{len(channels)} 频道）")
        else:
            label = video_id if platform == "bili" else video_id[:12]
            _log(task, "info", f"⬇️ 开始下载 [{label}] {title[:50]}")

        output_path = prepare_output_path(platform, video_id, title)
        ok, err, skip = False, "下载失败", False
        dl_ms = 0

        if is_valid_local_video(output_path):
            ok, err, skip = True, "", False
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            _log(task, "info", f"复用已下载文件 ({size_mb:.1f}MB)，跳过下载")
        elif partial_post:
            ok, err, skip = False, "续发文件缺失", False
        else:
            if download_manager.is_busy():
                _set_video(task, vi, status=VIDEO_PENDING, message="排队等待下载...")
                _log(task, "info", "其他视频下载中，排队等待…")

            def _mark_downloading() -> None:
                _set_video(task, vi, status=VIDEO_DOWNLOADING, message="正在下载...")

            try:
                result = await download_manager.run(
                    platform, video, output_path, on_acquire=_mark_downloading,
                )
                ok, err, skip = result.ok, result.err, result.skip
                dl_ms = result.elapsed_ms
            except asyncio.CancelledError:
                _reset_stale_downloading_item(task, vi) or _set_video(
                    task, vi, status=VIDEO_PENDING, message="下载已取消",
                )
                raise
            except Exception as e:
                ok, err, skip = False, str(e)[:120] or type(e).__name__, False
                _log(task, "error", f"下载异常 — {title[:30]}: {err}")
            else:
                if result.timed_out:
                    if ok:
                        size_mb = os.path.getsize(output_path) / (1024 * 1024)
                        _log(task, "info", f"下载超时但文件已就绪 ({size_mb:.1f}MB)，继续发帖")
                    else:
                        pid_note = f" · kill pid={result.killed_pid}" if result.killed_pid else ""
                        _log(task, "error", f"❌ 下载超时{pid_note} · {dl_ms}ms — {title[:30]}")
                elif ok:
                    size_mb = os.path.getsize(output_path) / (1024 * 1024) if os.path.exists(output_path) else 0
                    _log(task, "info", f"✅ 下载完成 {size_mb:.1f}MB · {dl_ms}ms")
                else:
                    _log(task, "warn", f"❌ 下载失败 · {dl_ms}ms · {(err or '')[:80]}")

        downloading = task.video_progress[vi].get("status") == VIDEO_DOWNLOADING
        if downloading or not ok:
            if skip or should_skip_download(err or ""):
                _mark_download_skipped(task, vi, err or "永久跳过", permanent=True)
                return [], [], False
            if not ok:
                err_msg = err or "下载失败"
                if is_proxy_error(err_msg):
                    _set_video(task, vi, status=VIDEO_PENDING, message="代理错误，待重试")
                    task.status = STATUS_PAUSED
                    _log(task, "error", f"代理错误，任务已暂停 — {title[:30]}")
                    _record_download_alert(task, vi, err_msg, note="任务已暂停")
                    _persist_tasks()
                    return [], [], True
                _mark_download_skipped(task, vi, err_msg, permanent=False)
                return [], [], False

        if _aborted(task_id):
            return [], [], True

        if skip or should_skip_download(err or ""):
            _mark_download_skipped(task, vi, err or "永久跳过", permanent=True)
            return [], [], False

        if not ok or not os.path.exists(output_path):
            err_msg = err or "下载失败"
            if is_proxy_error(err_msg):
                _set_video(task, vi, status=VIDEO_PENDING, message="代理错误，待重试")
                task.status = STATUS_PAUSED
                _log(task, "error", f"代理错误，任务已暂停 — {title[:30]}: {err_msg[:60]}")
                _record_download_alert(task, vi, err_msg, note="任务已暂停")
                _persist_tasks()
                return [], [], True
            _mark_download_skipped(task, vi, err_msg, permanent=False)
            return [], [], False

        task.video_progress[vi]["download_failures"] = 0
        task.video_progress[vi]["download_retry_until"] = 0

        size_mb = os.path.getsize(output_path) / (1024 * 1024)

        _set_video(task, vi, status=VIDEO_POSTING, account="随机轮换", message=f"下载完成 ({size_mb:.1f}MB)")

        posted, failed, stop_all = await _post_to_channels(
            task, vi, channels, output_path, title, account_ids
        )

        try:
            os.remove(output_path)
        except Exception:
            pass

        if _aborted(task_id):
            return posted, failed, True

        if failed and not posted:
            _set_video(task, vi, status=VIDEO_FAILED, message=f"全部频道失败 ({len(failed)})")
        elif failed:
            _set_video(task, vi, status=VIDEO_DONE, message=f"部分失败 ({len(failed)}/{len(channels)})")
        else:
            _set_video(task, vi, status=VIDEO_DONE, message=f"全部成功 ({len(posted)} 频道)")

        return posted, failed, stop_all

    if skip_lock:
        return await _run()

    lock = _get_video_process_lock(task_id, vi)
    async with lock:
        return await _run()


async def _task_watchdog():
    """检测 running 但协程已丢失的任务（如 Mac 休眠后），自动恢复。"""
    while True:
        try:
            await asyncio.sleep(30)
            for task_id, task in list(_tasks.items()):
                _recover_stale_downloading(task)
                if task.status != STATUS_RUNNING or task_id in _active_loops:
                    continue
                if not _should_kick_task_loop(task_id, task):
                    continue
                _recover_stale_waits(task)
                _log(task, "info", "检测到任务停滞，自动恢复执行")
                asyncio.create_task(_run_batch_task(task_id))
        except asyncio.CancelledError:
            break
        except Exception:
            pass


def _resume_running_tasks_on_startup(loop: asyncio.AbstractEventLoop) -> int:
    """服务重启后恢复重启前处于 running 的任务（与自动点赞一致）。"""
    resumed = 0
    for task_id, task in list(_tasks.items()):
        if task.status != STATUS_RUNNING:
            continue
        _paused.discard(task_id)
        _cancelled.discard(task_id)
        task.finished_at = ""
        _ensure_video_progress(task)
        _recover_stale_waits(task)
        _log(task, "info", "服务重启，自动恢复执行")
        loop.create_task(_run_batch_task(task_id))
        resumed += 1
    return resumed


def start_background_tasks():
    """应用启动时：加载任务、修复状态、恢复运行中任务，开启看门狗。"""
    import logging

    from backend.services.proxy_bypass import check_bilibili_direct

    global _watchdog_task
    ok, msg = check_bilibili_direct()
    if ok:
        logging.getLogger(__name__).info("B站直连自检通过")
    else:
        logging.getLogger(__name__).warning("B站直连自检失败: %s（下载任务遇代理错误将自动暂停）", msg)
    _load_tasks_from_disk()
    loop = asyncio.get_running_loop()
    for task in _tasks.values():
        _recover_stale_downloading(task)
        _repair_fatal_post_videos(task)
    resumed = _resume_running_tasks_on_startup(loop)
    if resumed:
        logging.getLogger(__name__).info("已自动恢复 %d 个运行中的任务", resumed)
    _persist_tasks()
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = loop.create_task(_task_watchdog())


async def stop_background_tasks():
    global _watchdog_task
    _persist_tasks()
    if _watchdog_task:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        _watchdog_task = None
