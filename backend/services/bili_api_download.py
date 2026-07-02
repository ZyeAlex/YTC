"""B站 API 直连下载（绕过 yt-dlp 拉取网页，用于网页超时时的兜底）。"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from backend.config import FFMPEG_PATH, MAX_SIZE_MB
from backend.services.bili_wbi import (
    _request_json_url,
    _request_json_url_with_headers,
    get_mixin_key,
    sign_wbi_query,
)
from backend.services.proxy_bypass import curl_no_proxy_args, sanitize_env

log = logging.getLogger(__name__)

VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_URL = "https://api.bilibili.com/x/player/wbi/playurl"
LEGACY_PLAYURL_URL = "https://api.bilibili.com/x/player/playurl"

_QN_FALLBACK = (80, 64, 32, 16)  # 1080p → 720p → 480p → 360p
_FNVAL_FALLBACK = (4048, 16, 1)


def _pick_url(item: dict | None) -> str:
    if not item:
        return ""
    url = item.get("baseUrl") or item.get("base_url") or item.get("url") or ""
    if url:
        return str(url)
    for backup in item.get("backupUrl") or item.get("backup_url") or []:
        if backup:
            return str(backup)
    return ""


def _streams_from_play_data(play_data: dict, *, max_height: int = 1080) -> tuple[dict | None, dict | None, str]:
    dash = play_data.get("dash") or {}
    videos = [v for v in (dash.get("video") or []) if _pick_url(v)]
    audios = [a for a in (dash.get("audio") or []) if _pick_url(a)]
    if videos:
        videos.sort(key=lambda v: (v.get("height") or 0, v.get("bandwidth") or 0), reverse=True)
        video = next((v for v in videos if (v.get("height") or 0) <= max_height), videos[-1])
        audio = max(audios, key=lambda a: a.get("bandwidth") or 0) if audios else None
        return video, audio, "dash"

    durls = [d for d in (play_data.get("durl") or []) if _pick_url(d)]
    if durls:
        best = max(durls, key=lambda d: d.get("size") or 0)
        return best, None, "durl"

    return None, None, ""


def _select_streams(play_data: dict, *, max_height: int = 1080) -> tuple[dict | None, dict | None]:
    video, audio, _ = _streams_from_play_data(play_data, max_height=max_height)
    return video, audio


def fetch_video_meta(bvid: str, cookie: str = "") -> tuple[dict | None, str | None]:
    data, err = _request_json_url(f"{VIEW_URL}?bvid={bvid}", cookie=cookie, timeout=25)
    if data is None:
        return None, err
    if data.get("code") != 0:
        return None, str(data.get("message") or f"view(code={data.get('code')})")
    payload = data.get("data") or {}
    pages = payload.get("pages") or []
    if not pages:
        return None, "视频无分P信息"
    page = pages[0]
    return {
        "bvid": payload.get("bvid") or bvid,
        "aid": payload.get("aid"),
        "cid": page.get("cid"),
        "title": payload.get("title") or "",
        "duration": payload.get("duration") or page.get("duration"),
    }, None


def fetch_playurl(
    *,
    bvid: str,
    cid: int,
    aid: int | None,
    cookie: str = "",
    qn: int = 80,
    fnval: int = 4048,
) -> tuple[dict | None, str | None]:
    params: dict[str, Any] = {
        "bvid": bvid,
        "cid": cid,
        "fnval": fnval,
        "fnver": 0,
        "fourk": 1,
        "qn": qn,
    }
    if aid:
        params["aid"] = aid

    mixin_key, key_err = get_mixin_key(cookie=cookie)
    if not mixin_key:
        return None, key_err or "WBI 密钥不可用"

    query = sign_wbi_query(params, mixin_key)
    page_headers = {
        "Referer": f"https://www.bilibili.com/video/{bvid}",
        "Origin": "https://www.bilibili.com",
    }
    data, err = _request_json_url_with_headers(
        f"{PLAYURL_URL}?{query}",
        cookie=cookie,
        timeout=25,
        extra_headers=page_headers,
    )
    if data is None:
        legacy_q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        data, err = _request_json_url_with_headers(
            f"{LEGACY_PLAYURL_URL}?{legacy_q}",
            cookie=cookie,
            timeout=25,
            extra_headers=page_headers,
        )
    if data is None:
        return None, err
    code = data.get("code")
    if code == 0:
        payload = data.get("data") or {}
        if payload.get("v_voucher"):
            return None, "B站风控验证(v_voucher)，请稍后重试"
        return payload, None
    return None, str(data.get("message") or f"playurl(code={code})")


def _select_streams(play_data: dict, *, max_height: int = 1080) -> tuple[dict | None, dict | None]:
    dash = play_data.get("dash") or {}
    videos = [v for v in (dash.get("video") or []) if _pick_url(v)]
    audios = [a for a in (dash.get("audio") or []) if _pick_url(a)]
    if not videos:
        return None, None
    videos.sort(key=lambda v: (v.get("height") or 0, v.get("bandwidth") or 0), reverse=True)
    video = next((v for v in videos if (v.get("height") or 0) <= max_height), videos[-1])
    audio = max(audios, key=lambda a: a.get("bandwidth") or 0) if audios else None
    return video, audio


def _curl_download(url: str, output_path: str, cookie: str = "") -> tuple[bool, str]:
    cmd = [
        "curl", "-L", "-sS", "-4",
        *curl_no_proxy_args(),
        "--max-time", "300",
        "-H", "Referer: https://www.bilibili.com/",
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ]
    if cookie:
        cmd.extend(["-H", f"Cookie: {cookie}"])
    cmd.extend(["-o", output_path, url])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=320, env=sanitize_env())
    except subprocess.TimeoutExpired:
        return False, "下载流超时"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "curl 失败").strip()
        return False, err[:200]
    if not os.path.exists(output_path) or os.path.getsize(output_path) < 50000:
        return False, "下载文件无效"
    return True, ""


def _merge_av(video_path: str, audio_path: str, output_path: str) -> tuple[bool, str]:
    ffmpeg = FFMPEG_PATH or "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", video_path,
        "-i", audio_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "合并音视频超时"
    if result.returncode != 0:
        return False, (result.stderr or "ffmpeg 合并失败")[:200]
    return True, ""


def download_bili_via_api(bvid: str, output_path: str, cookie: str = "") -> tuple[bool, str, bool]:
    """通过 view + playurl API 下载，返回 (ok, err, skip)。"""
    meta, err = fetch_video_meta(bvid, cookie)
    if meta is None:
        return False, err or "获取视频信息失败", False

    cid = meta.get("cid")
    aid = meta.get("aid")
    if not cid:
        return False, "缺少 cid", True

    last_err = ""
    for fnval in _FNVAL_FALLBACK:
        for qn in _QN_FALLBACK:
            play_data, perr = fetch_playurl(
                bvid=meta["bvid"],
                cid=int(cid),
                aid=int(aid) if aid else None,
                cookie=cookie,
                qn=qn,
                fnval=fnval,
            )
            if play_data is None:
                last_err = perr or "获取播放地址失败"
                continue

            video, audio, mode = _streams_from_play_data(
                play_data,
                max_height=1080 if qn >= 80 else 720 if qn >= 64 else 480,
            )
            if video is None:
                last_err = "无可用视频流"
                continue

            video_url = _pick_url(video)
            if mode == "durl":
                ok, verr = _curl_download(video_url, output_path, cookie)
                if not ok:
                    last_err = verr
                    continue
                if os.path.exists(output_path):
                    size_mb = os.path.getsize(output_path) / (1024 * 1024)
                    if size_mb > MAX_SIZE_MB:
                        os.remove(output_path)
                        return False, f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制", True
                    return True, "", False
                continue

            tmp_dir = os.path.dirname(output_path) or "."
            video_tmp = os.path.join(tmp_dir, f".{bvid}_v.m4s")
            audio_tmp = os.path.join(tmp_dir, f".{bvid}_a.m4s")

            for path in (video_tmp, audio_tmp, output_path):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

            ok, verr = _curl_download(video_url, video_tmp, cookie)
            if not ok:
                last_err = verr
                continue

            size_mb = os.path.getsize(video_tmp) / (1024 * 1024)
            if size_mb > MAX_SIZE_MB:
                os.remove(video_tmp)
                return False, f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制", True

            if audio and _pick_url(audio):
                ok, aerr = _curl_download(_pick_url(audio), audio_tmp, cookie)
                if not ok:
                    last_err = aerr
                    continue
                ok, merr = _merge_av(video_tmp, audio_tmp, output_path)
                for path in (video_tmp, audio_tmp):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                if not ok:
                    last_err = merr
                    continue
            else:
                try:
                    os.replace(video_tmp, output_path)
                except Exception as e:
                    last_err = str(e)
                    continue

            if os.path.exists(output_path):
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                if size_mb > MAX_SIZE_MB:
                    os.remove(output_path)
                    return False, f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制", True
                return True, "", False

    if "权限" in last_err or "412" in last_err or "404" in last_err:
        return False, last_err, True
    return False, last_err or "API 下载失败", False
