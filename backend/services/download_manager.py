"""下载调度：子进程 + kill + 单槽排队。"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass

from collections.abc import Callable

from backend.config import DOWNLOAD_WORKER_KILL_BUFFER, DOWNLOAD_WORKER_TIMEOUT, ROOT
from backend.services.download import is_valid_local_video

log = logging.getLogger(__name__)


@dataclass
class DownloadResult:
    ok: bool
    err: str = ""
    skip: bool = False
    elapsed_ms: int = 0
    timed_out: bool = False
    killed_pid: int | None = None


class DownloadManager:
    _instance: DownloadManager | None = None

    def __new__(cls) -> DownloadManager:
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._active_proc: asyncio.subprocess.Process | None = None
            inst._active_output_path: str | None = None
            inst._lock = asyncio.Lock()
            cls._instance = inst
        return cls._instance

    def is_busy(self) -> bool:
        proc = self._active_proc
        return proc is not None and proc.returncode is None

    async def run(
        self,
        platform: str,
        video: dict,
        output_path: str,
        *,
        on_acquire: Callable[[], None] | None = None,
    ) -> DownloadResult:
        async with self._lock:
            if on_acquire:
                on_acquire()
            return await self._run_locked(platform, video, output_path)

    async def _run_locked(
        self,
        platform: str,
        video: dict,
        output_path: str,
    ) -> DownloadResult:
        timeout = DOWNLOAD_WORKER_TIMEOUT.get(platform, 180)
        deadline_sec = max(30, timeout - DOWNLOAD_WORKER_KILL_BUFFER)
        payload = {
            "platform": platform,
            "video_id": video.get("id", ""),
            "link": video.get("link", ""),
            "output_path": output_path,
            "deadline_sec": deadline_sec,
        }
        cmd = [sys.executable, "-m", "backend.services.download_worker", json.dumps(payload, ensure_ascii=False)]
        t0 = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ROOT),
        )
        self._active_proc = proc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            pid = proc.pid
            proc.kill()
            await proc.wait()
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if is_valid_local_video(output_path):
                return DownloadResult(ok=True, elapsed_ms=elapsed_ms)
            log.warning("下载超时 · kill pid=%s · %dms", pid, elapsed_ms)
            return DownloadResult(
                ok=False,
                err="下载超时",
                elapsed_ms=elapsed_ms,
                timed_out=True,
                killed_pid=pid,
            )
        finally:
            self._active_proc = None

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        text = (stdout or b"").decode("utf-8", errors="replace").strip()
        if not text:
            err_tail = (stderr or b"").decode("utf-8", errors="replace").strip()[-200:]
            return DownloadResult(ok=False, err=err_tail or "下载 worker 无输出", elapsed_ms=elapsed_ms)

        try:
            data = json.loads(text.splitlines()[-1])
        except json.JSONDecodeError:
            return DownloadResult(ok=False, err="下载 worker 输出无效", elapsed_ms=elapsed_ms)

        ok = bool(data.get("ok"))
        err = str(data.get("err") or "")
        skip = bool(data.get("skip"))
        worker_ms = int(data.get("elapsed_ms") or elapsed_ms)
        if not ok and is_valid_local_video(output_path):
            ok = True
            err = ""
        return DownloadResult(ok=ok, err=err, skip=skip, elapsed_ms=worker_ms)


download_manager = DownloadManager()
