from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from backend.config import CLI_BINARY_PATH, PATH_ENV

log = logging.getLogger(__name__)

# 限制全局 CLI 并发，避免自动点赞 + 发帖任务同时打满 API
_cli_semaphore = threading.Semaphore(3)


def make_cli_env(token: str) -> tuple[dict[str, str], str]:
    """为 CLI 子进程构造隔离环境，避免继承宿主 ~/.qqcli 凭证。"""
    tmp_home = tempfile.mkdtemp(prefix="qqcli-home-")
    qqcli_dir = Path(tmp_home) / ".qqcli"
    qqcli_dir.mkdir(parents=True, exist_ok=True)
    (qqcli_dir / ".env").write_text(f'QQ_AI_CONNECT_TOKEN="{token}"\n', encoding="utf-8")

    env = {
        "PATH": PATH_ENV + os.defpath,
        "HOME": tmp_home,
        "LANG": os.environ.get("LANG", "C"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "QQ_AI_CONNECT_TOKEN": token,
        "QQ_AI_CONNECT_DOTENV": str(qqcli_dir / ".env"),
    }
    return env, tmp_home


def run_cli(token: str, args: list[str], *, timeout: int = 30) -> tuple[int, str]:
    """执行 CLI 命令，返回 (returncode, combined_output)。"""
    env, tmp_home = make_cli_env(token)
    acquired = _cli_semaphore.acquire(timeout=max(timeout, 60))
    if not acquired:
        return -1, "CLI 繁忙（并发任务过多），请稍后重试"
    try:
        result = subprocess.run(
            [CLI_BINARY_PATH, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.returncode, result.stdout + result.stderr
    finally:
        _cli_semaphore.release()
        shutil.rmtree(tmp_home, ignore_errors=True)
