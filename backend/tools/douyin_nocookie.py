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

    def _curl_max_time(self, max_time: int | None, deadline) -> int:
        if max_time is not None:
            base = max_time
        else:
            try:
                from backend.config import DOUYIN_CURL_MAX_TIME
                base = DOUYIN_CURL_MAX_TIME
            except ImportError:
                base = 15
        if deadline is not None:
            try:
                return max(5, min(base, int(deadline.remaining())))
            except Exception:
                pass
        return base

    def _curl(self, url: str, max_time: int | None = None, env: dict | None = None, deadline=None) -> str:
        """用 curl 获取 HTML，Popen 可被进程 kill 连带终止。"""
        max_time = self._curl_max_time(max_time, deadline)
        run_env = env
        if run_env is None:
            try:
                from backend.services.proxy_bypass import direct_connect_env
                run_env = direct_connect_env()
            except ImportError:
                run_env = os.environ.copy()
        cmd = [
            "curl", "-4", "-sS", url,
            *self._curl_base_headers(),
            "--noproxy", "*",
            "-L", "--max-redirs", "5",
            "--connect-timeout", "8",
            "--max-time", str(max_time),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=run_env,
            start_new_session=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=max_time + 10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=5)
            return ""
        return stdout or ""

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
            ["curl", "-4", "-sI", share_url,
             *self._curl_base_headers(),
             "--noproxy", "*",
             "--connect-timeout", "8",
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

    def _fetch_video_info_res(self, video_id: str, is_note: bool, deadline=None) -> dict | None:
        if is_note:
            url = f"https://www.iesdouyin.com/share/note/{video_id}/"
            page_key = "note_(id)/page"
        else:
            url = f"https://www.iesdouyin.com/share/video/{video_id}/"
            page_key = "video_(id)/page"

        html = self._curl(url, deadline=deadline)
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
        for attempt in range(2):
            reason = self._probe_web_detail_once(video_id)
            if reason:
                return reason
            if attempt < 1:
                time.sleep(0.2)
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

    def _resolve_parse(self, share_url: str, deadline=None) -> dict | None:
        """解析分享链接，优先识别不可观看（删除/私密）再返回正常结果。"""
        if deadline is not None:
            try:
                if deadline.expired():
                    return None
            except Exception:
                pass
        video_id = self.get_video_id(share_url)
        if not video_id:
            return None

        is_note = "/note/" in share_url
        res = self._fetch_video_info_res(video_id, is_note, deadline=deadline)
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

    def parse_video(self, share_url: str, deadline=None) -> dict:
        return self._resolve_parse(share_url, deadline=deadline)

    def download(
        self,
        share_url: str,
        output_dir: str = "/tmp/bili_video",
        env: dict | None = None,
        deadline=None,
    ) -> dict:
        if deadline is not None:
            try:
                if deadline.expired():
                    return {"error": "下载超时"}
            except Exception:
                pass

        last_err = "解析或下载失败"
        for attempt in range(2):
            info = self._resolve_parse(share_url, deadline=deadline)
            if info and info.get("skip"):
                return {"skip": True, "error": info.get("reason", "视频不可下载")}
            if not info:
                last_err = "解析失败，无法获取页面数据"
                continue
            if not info.get("nwm_url"):
                last_err = "解析失败，无法获取视频 URL"
                continue

            video_id = info["video_id"]
            nwm_url = info["nwm_url"]
            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, f"dy_{video_id}.mp4")

            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass

            dl_max = self._curl_max_time(90, deadline)
            try:
                from backend.services.curl_utils import curl_download_file

                ok, err = curl_download_file(
                    nwm_url,
                    out_path,
                    cookie=self.cookie,
                    referer="https://www.iesdouyin.com/",
                    timeout=dl_max,
                    min_bytes=10_000,
                )
            except ImportError:
                ok, err = False, "curl 工具不可用"

            if ok and os.path.exists(out_path):
                return {"path": out_path, "info": info}

            last_err = err or "下载失败，文件太小或不存在"
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            if attempt == 0 and last_err in (
                "下载失败，文件太小或不存在",
                "下载内容非 MP4 视频",
            ):
                continue
            break

        return {"error": last_err}


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
