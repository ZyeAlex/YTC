"""下载子进程 CLI worker：独立进程内执行下载，可被外层 kill。"""

from __future__ import annotations

import json
import sys
import time

from backend.services.download import download_bili, download_douyin, is_valid_local_video


def main() -> None:
    if len(sys.argv) > 1:
        payload = json.loads(sys.argv[1])
    else:
        payload = json.load(sys.stdin)

    platform = payload["platform"]
    video_id = payload.get("video_id", "")
    link = payload.get("link", "")
    output_path = payload["output_path"]
    deadline_sec = payload.get("deadline_sec")

    t0 = time.perf_counter()
    ok, err, skip = False, "未知平台", False

    if platform == "bili":
        ok, err, skip = download_bili(video_id, output_path, deadline_sec=deadline_sec)
    elif platform == "douyin":
        ok, err, skip = download_douyin(link, output_path, deadline_sec=deadline_sec)
    else:
        err = f"未知平台: {platform}"

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not ok and is_valid_local_video(output_path):
        ok, err, skip = True, "", False

    result = {"ok": ok, "err": err or "", "skip": skip, "elapsed_ms": elapsed_ms}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
