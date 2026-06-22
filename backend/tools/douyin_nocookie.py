#!/usr/bin/env python3
"""
抖音无水印视频解析 - 无需 Cookie 版本
通过移动端分享页面提取视频信息，完全不需要登录或 Cookie

原理：
1. 访问分享短链接，获取重定向后的长 URL（含 video_id）
2. 访问移动端分享页面 iesdouyin.com/share/video/{video_id}/
3. 从 HTML 中提取 window._ROUTER_DATA 内的 JSON 数据
4. 找到 play_addr.url_list，将 playwm 替换为 play 得到无水印 URL

依赖：requests（uv pip install requests）
"""

import re
import subprocess
import json
import os
import sys
import time


class DouyinNoCookieParser:
    """抖音无 Cookie 解析器"""

    def __init__(self, cookie: str = ""):
        self.cookie = (cookie or "").strip()
        self.mobile_ua = (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        )

    def _curl_base_headers(self) -> list[str]:
        headers = ["-H", f"User-Agent: {self.mobile_ua}"]
        if self.cookie:
            headers.extend(["-H", f"Cookie: {self.cookie}"])
        return headers

    def _curl(self, url: str, max_time: int = 30, env: dict | None = None) -> str:
        """用 curl 获取 HTML，避免 requests 的 SSL 问题"""
        run_env = env
        if run_env is None:
            try:
                from backend.services.proxy_bypass import direct_connect_env
                run_env = direct_connect_env()
            except ImportError:
                run_env = os.environ.copy()
        no_proxy = ["--noproxy", "*"]
        result = subprocess.run(
            ["curl", "-s", url,
             *self._curl_base_headers(),
             *no_proxy,
             "-L", "--max-redirs", "5",
             "--connect-timeout", "10",
             "--max-time", str(max_time)],
            capture_output=True, text=True,
            env=run_env,
        )
        return result.stdout

    def get_video_id(self, share_url: str) -> str:
        """从分享链接提取视频 ID（支持 video 和 note 类型）"""
        patterns = [
            r'/video/(\d+)',
            r'/share/video/(\d+)',
            r'/note/(\d+)',
            r'/share/note/(\d+)',
            r'video_id=(\d+)',
            r'aweme_id=(\d+)',
        ]
        # 先尝试正则直接匹配
        for p in patterns:
            m = re.search(p, share_url)
            if m:
                return m.group(1)

        # 短链接：用 curl -sI 获取 302 location 头，从中提取 video_id
        try:
            from backend.services.proxy_bypass import direct_connect_env
            run_env = direct_connect_env()
        except ImportError:
            run_env = os.environ.copy()
        result = subprocess.run(
            ["curl", "-sI", share_url,
             *self._curl_base_headers(),
             "--noproxy", "*",
             "--max-time", "15"],
            capture_output=True, text=True,
            env=run_env,
        )
        location = ""
        for line in result.stdout.split('\n'):
            if line.lower().startswith('location:'):
                location = line.split(':', 1)[1].strip()
                break
        if location:
            for p in patterns:
                m = re.search(p, location)
                if m:
                    return m.group(1)
        return ""

    def _filter_reason(self, video_info_res: dict) -> str:
        """页面无 item_list 时，从 filter_list 提取抖音返回的不可观看原因。"""
        fl = video_info_res.get("filter_list") or []
        if not fl or not isinstance(fl[0], dict):
            return ""
        item = fl[0]
        return (
            item.get("detail_msg")
            or item.get("notice")
            or item.get("filter_reason")
            or ""
        ).strip()

    def _unavailable_reason(self, video_info_res: dict) -> str:
        """作品已删除、私密或不可观看时返回原因，否则空字符串。"""
        reason = self._filter_reason(video_info_res)
        if reason:
            return reason
        fl = video_info_res.get("filter_list") or []
        if fl and isinstance(fl[0], dict):
            code = str(fl[0].get("filter_reason", "")).strip()
            if code in ("status_self_see", "status_delete", "status_friend_see"):
                return fl[0].get("detail_msg") or fl[0].get("notice") or "视频不可观看"
        return ""

    def _fetch_video_info_res(self, video_id: str, is_note: bool) -> dict | None:
        if is_note:
            url = f"https://www.iesdouyin.com/share/note/{video_id}/"
            page_key = "note_(id)/page"
        else:
            url = f"https://www.iesdouyin.com/share/video/{video_id}/"
            page_key = "video_(id)/page"

        html = self._curl(url)
        start = html.find("window._ROUTER_DATA = ")
        if start < 0 and "</script>" not in html:
            html = self._curl(url, max_time=35)
            start = html.find("window._ROUTER_DATA = ")
        if start < 0:
            return None

        brace_start = start + len("window._ROUTER_DATA = ")
        if html[brace_start] in ("'", '"'):
            brace_start += 1
        script_close = html.find("</script>", brace_start)
        if script_close < 0:
            return None
        last_brace = html.rfind("}", brace_start, script_close)
        if last_brace < 0:
            return None
        try:
            data = json.loads(html[brace_start:last_brace + 1])
        except json.JSONDecodeError:
            return None
        page_data = data.get("loaderData", {}).get(page_key, {})
        return page_data.get("videoInfoRes", {})

    def _probe_web_detail(self, video_id: str) -> str:
        """页面未带 filter_list 时，用 web detail 接口确认是否已删除/私密。"""
        for attempt in range(3):
            reason = self._probe_web_detail_once(video_id)
            if reason:
                return reason
            if attempt < 2:
                time.sleep(0.5)
        return ""

    def _probe_web_detail_once(self, video_id: str) -> str:
        api = (
            f"https://www.douyin.com/aweme/v1/web/aweme/detail/"
            f"?aweme_id={video_id}&aid=6383&device_platform=webapp"
        )
        try:
            from backend.services.proxy_bypass import direct_connect_env
            run_env = direct_connect_env()
        except ImportError:
            run_env = os.environ.copy()
        hdr = [
            "-H",
            "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        if self.cookie:
            hdr.extend(["-H", f"Cookie: {self.cookie}", "-H", "referer: https://www.douyin.com/"])
        result = subprocess.run(
            ["curl", "-s", api, *hdr, "--noproxy", "*", "--max-time", "20"],
            capture_output=True,
            text=True,
            env=run_env,
        )
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return ""
        detail = data.get("filter_detail") or {}
        if isinstance(detail, dict) and detail:
            return (
                detail.get("detail_msg")
                or detail.get("notice")
                or detail.get("filter_reason")
                or ""
            ).strip()
        # aweme_detail 为空且无 filter_detail 时，部分响应仍表示不可看
        if data.get("aweme_detail") is None and data.get("status_code") == 0:
            status_msg = str(data.get("status_msg") or "").strip()
            if status_msg:
                return status_msg
        return ""

    def _resolve_parse(self, share_url: str) -> dict | None:
        """解析分享链接，优先识别不可观看（删除/私密）再返回正常结果。"""
        video_id = self.get_video_id(share_url)
        if not video_id:
            return None

        is_note = "/note/" in share_url
        res = None
        for attempt in range(5):
            res = self._fetch_video_info_res(video_id, is_note)
            if res:
                items = res.get("item_list", [])
                if items:
                    info = self._aweme_to_info(items[0], video_id, is_note)
                    if info:
                        return info
                reason = self._unavailable_reason(res)
                if reason:
                    return {"skip": True, "reason": reason}
            api_reason = self._probe_web_detail(video_id)
            if api_reason:
                return {"skip": True, "reason": api_reason}
            if attempt < 4:
                time.sleep(0.6)

        if res:
            reason = self._unavailable_reason(res)
            if reason:
                return {"skip": True, "reason": reason}
        api_reason = self._probe_web_detail(video_id)
        if api_reason:
            return {"skip": True, "reason": api_reason}
        return None

    def _aweme_to_info(self, aweme: dict, video_id: str, is_note: bool) -> dict:
        video_id = aweme.get("aweme_id", video_id)
        desc = aweme.get("desc", "")

        author_data = aweme.get("author", {})
        author = ""
        if isinstance(author_data, dict):
            author = author_data.get("nickname", "")
        elif isinstance(author_data, str):
            author = author_data

        cover_data = aweme.get("video", {}).get("cover", {})
        if isinstance(cover_data, dict):
            urls = cover_data.get("url_list", [])
            cover_url = urls[0] if urls else ""
        elif isinstance(cover_data, str):
            cover_url = cover_data
        else:
            cover_url = ""

        url_list = aweme.get("video", {}).get("play_addr", {}).get("url_list", [])
        if not url_list:
            return None
        wm_url = url_list[0]
        if ".mp3" in wm_url.lower():
            return {"skip": True, "reason": "图文帖(aweme_type=2)，无视频可下载"}
        nwm_url = wm_url.replace("/playwm/", "/play/")

        return {
            "video_id": video_id,
            "desc": desc,
            "author": author,
            "cover_url": cover_url,
            "nwm_url": nwm_url,
            "type": "note" if is_note else "video",
        }

    def parse_video(self, share_url: str) -> dict:
        """
        解析抖音视频（无需 Cookie，支持 video 和 note 类型）

        返回示例：
        {
            "video_id": "7630854312982382713",
            "desc": "异环的车辆破坏系统 #异环 #异环手游",
            "author": "1emon",
            "cover_url": "https://...",
            "nwm_url": "https://aweme.snssdk.com/aweme/v1/play/?...",
            "type": "video" | "note",
        }
        返回 None 表示解析失败
        """
        return self._resolve_parse(share_url)

    def download(self, share_url: str, output_dir: str = "/tmp/bili_video", env: dict | None = None) -> dict:
        """
        解析并下载视频，返回本地文件路径

        返回 {"path": "...} 或 {"error": "..."}
        """
        info = None
        for attempt in range(3):
            info = self._resolve_parse(share_url)
            if info:
                break
            if attempt < 2:
                time.sleep(0.8)
        if info and info.get("skip"):
            return {"skip": True, "error": info.get("reason", "视频不可下载")}
        if not info:
            return {"error": "解析失败，无法获取页面数据"}
        if not info.get("nwm_url"):
            return {"error": "解析失败，无法获取视频 URL"}

        video_id = info["video_id"]
        nwm_url = info["nwm_url"]

        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"dy_{video_id}.mp4")

        if env is None:
            try:
                from backend.services.proxy_bypass import direct_connect_env
                env = direct_connect_env()
            except ImportError:
                env = os.environ.copy()

        head = subprocess.run(
            ["curl", "-sI", "-L", "--max-time", "15", "--noproxy", "*",
             *self._curl_base_headers(),
             "-H", "referer: https://www.iesdouyin.com/",
             nwm_url],
            capture_output=True, text=True,
            env=env,
        )
        if head.returncode == 0:
            for line in head.stdout.splitlines():
                if line.lower().startswith("content-length:"):
                    try:
                        size_mb = int(line.split(":", 1)[1].strip()) / (1024 * 1024)
                        from backend.config import MAX_SIZE_MB
                        if size_mb > MAX_SIZE_MB:
                            return {
                                "skip": True,
                                "error": f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制",
                            }
                    except ValueError:
                        pass
                    break

        subprocess.run(
            ["curl", "-L", nwm_url,
             *self._curl_base_headers(),
             "--noproxy", "*",
             "-H", "referer: https://www.iesdouyin.com/",
             "-o", out_path,
             "--max-time", "120"],
            capture_output=True, text=True,
            env=env,
        )

        if os.path.exists(out_path) and os.path.getsize(out_path) > 10000:
            return {"path": out_path, "info": info}
        else:
            return {"error": "下载失败，文件太小或不存在"}


def parse_douyin_video(share_url: str) -> dict:
    """简化调用：输入分享链接，返回视频信息"""
    return DouyinNoCookieParser().parse_video(share_url)


if __name__ == "__main__":
    test_url = "https://v.douyin.com/aeFHSZRqE14/"
    if len(sys.argv) > 1:
        test_url = sys.argv[1]

    result = parse_douyin_video(test_url)
    if result and result.get("skip"):
        print(f"⏭ 跳过: {result.get('reason', '视频不可下载')}")
    elif result and result.get("video_id"):
        print("✅ 解析成功！")
        print(f"视频ID: {result['video_id']}")
        print(f"作者: {result['author']}")
        print(f"描述: {result['desc'][:80]}")
        print(f"无水印URL: {result['nwm_url']}")
    else:
        print("❌ 解析失败")
        sys.exit(1)
