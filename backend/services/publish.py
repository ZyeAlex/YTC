from __future__ import annotations

import random
import re
import shutil
import subprocess
import time

from backend.config import CLI_BINARY_PATH
from backend.data.accounts import get_account_label, get_token
from backend.services.cli_env import make_cli_env

# #话题 → #[话题]()（腾讯频道短帖内联话题）
# 兼容：「#a #b」「#a#b#c」「#a# #b#」「#a##b#」等 # 作分隔的写法
_TOPIC_TAG_RE = re.compile(r"#(?!\[)([^\s#]+)(?=#|\s|$)")
_FORMATTED_TOPIC_RE = re.compile(r"#\[[^\]]+\]\(\)")


def format_feed_content(content: str) -> str:
    """将正文中的 #标签 转为腾讯频道话题语法 #[标签]()。"""
    text = (content or "").strip()
    if not text:
        return text
    text = _TOPIC_TAG_RE.sub(r"#[\1]()", text)
    m = re.search(r"#\[", text)
    if not m:
        return text
    pos = m.start()
    if pos == 0 or not text[pos - 1].isspace():
        text = text[:pos] + " " + text[pos:]
    return text


def strip_topics_from_content(content: str) -> str:
    """移除正文中的话题标签（含已格式化的 #[话题]()）。"""
    text = (content or "").strip()
    if not text:
        return text
    text = _FORMATTED_TOPIC_RE.sub("", text)
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "#" and (i + 1 >= n or text[i + 1] != "["):
            j = i + 1
            start = j
            while j < n and text[j] not in " \t\n\r#":
                j += 1
            if start < j:
                i = j
                continue
            i += 1
            continue
        out.append(text[i])
        i += 1
    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def prepare_publish_content(content: str, *, include_topics: bool = True) -> str:
    text = (content or "").strip()
    if not text:
        return text
    if not include_topics:
        return strip_topics_from_content(text)
    return format_feed_content(text)


def _cli_env(token: str) -> tuple[dict[str, str], str]:
    return make_cli_env(token)


def _cli_error_detail(output: str) -> str:
    text = (output or "").strip()
    if not text:
        return "CLI 无输出"
    for pattern in (
        r'"message"\s*:\s*"([^"]+)"',
        r'"msg"\s*:\s*"([^"]+)"',
        r"错误码\s*\d+\s*[：:]\s*([^\n\"]+)",
    ):
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()[:160]
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line or "NotOpenSSLWarning" in line:
            continue
        if any(k in line for k in ("error", "Error", "错误", "失败", "code")):
            return line[:160]
    return text[-160:]


def publish_video(
    guild_id: str,
    channel_id: str,
    video_path: str,
    content: str,
    account_id: str,
    *,
    include_topics: bool = True,
) -> tuple[bool, str, str]:
    """
    发布视频到腾讯频道
    返回 (success, error_type, detail):
    error_type 为 "" | "rate_limit" | "oidb_limit" | "permission" | "banned" | "other"
    """
    token = get_token(account_id)
    if not token:
        return False, "other", "账号 Token 无效"

    body = prepare_publish_content(content, include_topics=include_topics)
    cmd = [
        CLI_BINARY_PATH, "feed", "publish-feed",
        "--guild-id", guild_id,
        "--channel-id", channel_id,
        "--content", body,
        "--video", video_path,
        "--json", "--yes",
    ]
    env, tmp_home = _cli_env(token)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        output = result.stdout + result.stderr
        if result.returncode == 0 and ('"feed_id"' in output or '"id"' in output):
            return True, "", ""
        detail = _cli_error_detail(output)
        if '"code":20063' in output or "错误码 20063" in output:
            return False, "rate_limit", detail
        if '"code":153' in output:
            return False, "oidb_limit", detail
        if "错误码 10023" in output or '"code":10023' in output:
            return False, "permission", detail
        if "错误码 890500" in output or '"code":890500' in output:
            return False, "banned", detail
        if '"code":10000' in output or "错误码 10000" in output:
            return False, "content_rejected", detail
        return False, "other", detail
    except Exception as e:
        return False, "other", str(e)[:160]
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
