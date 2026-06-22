from __future__ import annotations

from backend.data.app_config import get_filter_patterns_data, save_filter_patterns_data


def get_filter_patterns() -> list[str]:
    return get_filter_patterns_data()


def save_filter_patterns(patterns: list[str]) -> list[str]:
    return save_filter_patterns_data(patterns)
