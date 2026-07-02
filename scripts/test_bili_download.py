#!/usr/bin/env python3
"""本地测试 B 站下载（读取 config/config.json 中的 Cookie，不打印 Cookie 内容）。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.app_config import get_bili_cookies
from backend.services.download import _probe_bili_info, _cookie_file, download_bili


def main() -> int:
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1GeTi63EAK"
    do_download = "--download" in sys.argv
    cookies = get_bili_cookies()
    print(f"测试视频: {bvid}")
    print(f"已配置 Cookie 数量: {len(cookies)}")

    url = f"https://www.bilibili.com/video/{bvid}"
    cookie = _cookie_file()
    info, err = _probe_bili_info(url, cookie)
    if info:
        print("探测成功:", (info.get("title") or "")[:60])
    else:
        print("探测失败:", err)
        if not do_download:
            return 1

    if not do_download:
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        output = str(Path(tmp) / f"{bvid}.mp4")
        ok, dl_err, skip = download_bili(bvid, output)
        if ok:
            size_mb = Path(output).stat().st_size / (1024 * 1024)
            print(f"下载成功: {size_mb:.1f} MB")
            return 0
        print("下载失败:", dl_err, "(skip)" if skip else "")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
