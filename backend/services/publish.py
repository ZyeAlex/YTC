from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from backend.config import CLI_BINARY_PATH, PATH_ENV
from backend.data.accounts import get_account_label, get_token
from backend.services.cli_env import make_cli_env


def _cli_env(token: str) -> tuple[dict[str, str], str]:
    return make_cli_env(token)


def publish_video(
    guild_id: str,
    channel_id: str,
    video_path: str,
    content: str,
    account_id: str,
) -> tuple[bool, str]:
    """
    发布视频到腾讯频道
    返回 (success, error_type): error_type 为 "" | "rate_limit" | "oidb_limit" | "permission" | "other"
    """
    token = get_token(account_id)
    if not token:
        return False, "other"

    cmd = [
        CLI_BINARY_PATH, "feed", "publish-feed",
        "--guild-id", guild_id,
        "--channel-id", channel_id,
        "--content", content,
        "--video", video_path,
        "--json", "--yes",
    ]
    env, tmp_home = _cli_env(token)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        output = result.stdout + result.stderr
        if result.returncode == 0 and ('"feed_id"' in output or '"id"' in output):
            return True, ""
        if '"code":20063' in output or "错误码 20063" in output:
            return False, "rate_limit"
        if '"code":153' in output:
            return False, "oidb_limit"
        if "错误码 10023" in output or '"code":10023' in output:
            return False, "permission"
        return False, "other"
    except Exception:
        return False, "other"
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


def pick_random_account(account_ids: list[str]) -> str:
    return random.choice(account_ids)


def account_label(account_id: str) -> str:
    return get_account_label(account_id)


def wait_interval(min_sec: int, max_sec: int):
    if min_sec <= 0 and max_sec <= 0:
        return
    lo = max(0, min_sec)
    hi = max(lo, max_sec)
    time.sleep(random.randint(lo, hi))
