"""下载 deadline 工具（子进程内使用）。"""

from __future__ import annotations

import time


class DownloadDeadline:
    __slots__ = ("_deadline",)

    def __init__(self, seconds: float | None) -> None:
        self._deadline = time.monotonic() + seconds if seconds and seconds > 0 else None

    def expired(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def remaining(self) -> float:
        if self._deadline is None:
            return 86400.0
        return max(0.0, self._deadline - time.monotonic())

    def curl_timeout(self, default: int = 60) -> int:
        return max(5, min(default, int(self.remaining())))
