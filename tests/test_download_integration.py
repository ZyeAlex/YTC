"""下载链路集成测试（需网络）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.curl_utils import curl_get_json, is_valid_mp4_file
from backend.services.download import download_bili, download_douyin, is_valid_local_video

# 稳定公开样本（B站小视频 + 抖音分享页）
BILI_BVID = "BV1MJTZ6cEbi"
DOUYIN_LINK = "https://www.iesdouyin.com/share/video/7657912550759084934"
BILI_DEADLINE_SEC = 200


class TestCurlUtils(unittest.TestCase):
    def test_bili_view_api(self):
        data, err = curl_get_json(
            f"https://api.bilibili.com/x/web-interface/view?bvid={BILI_BVID}",
            timeout=20,
            retries=3,
        )
        self.assertIsNotNone(data, err)
        self.assertEqual(data.get("code"), 0)


class TestDownloadBili(unittest.TestCase):
    def test_download_bili_video(self):
        tmp = tempfile.mkdtemp(prefix="dltest_bili_")
        out = os.path.join(tmp, "test.mp4")
        t0 = time.perf_counter()
        ok, err, skip = download_bili(BILI_BVID, out, deadline_sec=BILI_DEADLINE_SEC)
        elapsed = time.perf_counter() - t0
        self.assertFalse(skip, f"不应 skip: {err}")
        self.assertTrue(ok, f"下载失败({elapsed:.0f}s): {err}")
        self.assertTrue(is_valid_local_video(out), "非有效 mp4")
        size = os.path.getsize(out)
        self.assertGreater(size, 100_000, f"文件过小: {size}")


class TestDownloadDouyin(unittest.TestCase):
    def test_download_douyin_video(self):
        tmp = tempfile.mkdtemp(prefix="dltest_dy_")
        out = os.path.join(tmp, "test.mp4")
        t0 = time.perf_counter()
        ok, err, skip = download_douyin(DOUYIN_LINK, out, deadline_sec=90)
        elapsed = time.perf_counter() - t0
        if skip:
            self.skipTest(f"视频不可下载: {err}")
        self.assertTrue(ok, f"下载失败({elapsed:.0f}s): {err}")
        self.assertTrue(is_valid_mp4_file(out), "非有效 mp4")


class TestDownloadWorker(unittest.TestCase):
    def test_worker_cli_bili(self):
        tmp = tempfile.mkdtemp(prefix="dltest_worker_")
        out = os.path.join(tmp, "w.mp4")
        payload = json.dumps({
            "platform": "bili",
            "video_id": BILI_BVID,
            "link": "",
            "output_path": out,
            "deadline_sec": BILI_DEADLINE_SEC,
        })
        t0 = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "backend.services.download_worker", payload],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=300,
        )
        elapsed = time.perf_counter() - t0
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = proc.stdout.strip().splitlines()[-1]
        data = json.loads(line)
        self.assertTrue(data.get("ok"), data.get("err"))
        self.assertTrue(is_valid_local_video(out), f"worker 输出无效 ({elapsed:.0f}s)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
