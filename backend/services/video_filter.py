from __future__ import annotations

import re

# 每行一条正则，标题命中则排除
DEFAULT_FILTER_PATTERNS: list[str] = [
    # 游戏低质 / 引流
    r"加群",
    r"代肝",
    r"代练",
    r"代打",
    r"开挂",
    r"外挂",
    r"刷分",
    r"刷币",
    r"陪玩",
    r"上分",
    r"带飞",
    r"福利群",
    r"看主页",
    r"看简介",
    r"工作室",
    r"接单",
    r"礼包",
    r"领皮肤",
    r"免费领",
    r"充值",
    r"便宜出",
    # 化妆 / 美妆
    r"化妆",
    r"美妆",
    r"护肤",
    r"口红",
    r"粉底",
    r"眼影",
    r"腮红",
    r"卸妆",
    r"彩妆",
    r"妆容",
    r"试色",
    r"种草",
]


def compile_filter_patterns(lines: list[str]) -> tuple[list[re.Pattern[str]], list[str]]:
    patterns: list[re.Pattern[str]] = []
    errors: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error as e:
            errors.append(f"{line}: {e}")
    return patterns, errors


def title_matches_filter(title: str, patterns: list[re.Pattern[str]]) -> bool:
    if not patterns:
        return False
    return any(p.search(title or "") for p in patterns)


def filter_videos(videos: list[dict], patterns: list[re.Pattern[str]]) -> tuple[list[dict], int]:
    if not patterns:
        return videos, 0
    kept: list[dict] = []
    filtered = 0
    for video in videos:
        if title_matches_filter(video.get("title", ""), patterns):
            filtered += 1
        else:
            kept.append(video)
    return kept, filtered
