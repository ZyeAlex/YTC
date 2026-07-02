#!/usr/bin/env python3
"""便携版入口：双击 exe 启动 Web 服务并打开浏览器。"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser


def _bootstrap() -> None:
    if getattr(sys, "frozen", False):
        os.chdir(str(os.path.dirname(sys.executable)))

    import backend.config  # noqa: F401 — 初始化路径、ffmpeg、yt-dlp shim


def main() -> None:
    _bootstrap()

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))

    if os.environ.get("OPEN_BROWSER", "1") == "1":

        def _open_browser() -> None:
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open_browser, daemon=True).start()

    import uvicorn

    print("========================================")
    print("  腾讯频道发帖工具")
    print("========================================")
    print(f"🚀 服务地址: http://{host}:{port}")
    print("   关闭此窗口即可停止服务")
    print("")

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
