"""下载逻辑单元测试（无网络）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.services.curl_utils import is_valid_mp4_file
from backend.services.download import should_skip_download
from backend.services.download_deadline import DownloadDeadline


class TestDownloadHelpers(unittest.TestCase):
    def test_should_skip_oversize(self):
        self.assertTrue(should_skip_download("视频约 240.2MB 超过 200MB 限制"))

    def test_should_skip_deleted(self):
        self.assertTrue(should_skip_download("作品不见了"))

    def test_should_not_skip_timeout(self):
        self.assertFalse(should_skip_download("连接 B 站超时"))

    def test_deadline_remaining(self):
        dl = DownloadDeadline(10.0)
        self.assertGreater(dl.remaining(), 9.0)
        self.assertFalse(dl.expired())

    def test_is_valid_mp4_rejects_small(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            tmp.write(b"tiny")
            tmp.close()
            self.assertFalse(is_valid_mp4_file(tmp.name))
        finally:
            os.unlink(tmp.name)

    def test_is_valid_mp4_accepts_ftyp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        try:
            tmp.write(b"\x00" * 4 + b"ftyp" + b"\x00" * 60_000)
            tmp.close()
            self.assertTrue(is_valid_mp4_file(tmp.name, min_bytes=50_000))
        finally:
            os.unlink(tmp.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
