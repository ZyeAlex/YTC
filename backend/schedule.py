"""Cron-based video send scheduling."""

from __future__ import annotations

from datetime import datetime
import time

from croniter import croniter


def validate_cron(cron: str) -> bool:
    if not cron or not cron.strip():
        return True
    try:
        croniter(cron.strip())
        return True
    except (ValueError, KeyError):
        return False


def next_run_timestamp(cron: str, after: float | None = None) -> float:
    base = datetime.fromtimestamp(after if after is not None else time.time())
    it = croniter(cron.strip(), base)
    # get_next(float) 会把返回值当成错误类型；必须用 datetime 再转时间戳
    return it.get_next(datetime).timestamp()


def seconds_until_next(cron: str, after: float | None = None) -> tuple[int, float]:
    next_ts = next_run_timestamp(cron, after)
    wait = max(0, int(next_ts - time.time()))
    return wait, next_ts


def format_next_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def describe_cron(cron: str) -> str:
    if not cron or not cron.strip():
        return "无计划（连续发送）"
    parts = cron.strip().split()
    if len(parts) != 5:
        return cron
    mins, hrs, dom, mon, dow = parts
    if not (dom == mon == dow == "*"):
        return cron

    minute_label = f":{int(mins):02d}" if mins.isdigit() else f"分 {mins}"

    # 按小时（超过一小时的间隔 / 指定时刻）
    if hrs != "*":
        if hrs.startswith("*/"):
            return f"每 {hrs[2:]} 小时的 {minute_label}"
        if "/" in hrs:
            start, step = hrs.split("/", 1)
            if start in ("0", "*"):
                return f"每 {step} 小时的 {minute_label}"
            return f"从 {start} 时起每 {step} 小时的 {minute_label}"
        if "," in hrs or hrs.isdigit():
            hlist = sorted(int(x) for x in hrs.split(","))
            if mins.isdigit():
                times = "、".join(f"{h:02d}{minute_label}" for h in hlist)
                return f"每天 {times}"
            return f"在 {hrs} 时的 {minute_label}"
        return f"{hrs} 时 {minute_label}"

    # 按分钟（每小时内）
    if mins == "*":
        return "每分钟"
    if mins.startswith("*/"):
        return f"每 {mins[2:]} 分钟"
    if "/" in mins:
        start, step = mins.split("/", 1)
        if start in ("0", "*"):
            return f"每 {step} 分钟"
        return f"从 {start} 分起，每 {step} 分钟"
    if "," in mins:
        mlist = sorted(int(x) for x in mins.split(","))
        times = "、".join(f":{m:02d}" for m in mlist)
        return f"每小时 {times}"
    if mins.isdigit():
        return f"每小时 {minute_label}"
    return cron


def cron_from_legacy_interval(start_minute: int, interval_minutes: float) -> str:
    """一次性迁移：旧起始分钟 + 间隔 → cron。"""
    interval = int(interval_minutes)
    if interval <= 0:
        return ""
    start = int(start_minute) % 60
    minutes: set[int] = set()
    m = start
    for _ in range(60):
        minutes.add(m)
        m = (m + interval) % 60
        if m == start and len(minutes) > 1:
            break
    ordered = sorted(minutes)
    return f"{','.join(str(x) for x in ordered)} * * * *"


def stagger_start_minute(index: int, task_id: str = "") -> int:
    base = (index * 7) % 60
    if task_id:
        base = (base + sum(ord(c) for c in task_id[:8])) % 60
    return base


def default_cron_for_task(index: int, task_id: str = "", interval: int = 20) -> str:
    return cron_from_legacy_interval(stagger_start_minute(index, task_id), interval)
