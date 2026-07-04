"""B站 API 直连下载。"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from backend.config import FFMPEG_PATH, MAX_SIZE_MB, BILI_API_TIMEOUT
from backend.services.bili_wbi import get_mixin_key, request_bili_api_json, sign_wbi_query
from backend.services.download_deadline import DownloadDeadline
from backend.services.proxy_bypass import curl_no_proxy_args, sanitize_env

log = logging.getLogger(__name__)

VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_URL = "https://api.bilibili.com/x/player/wbi/playurl"
LEGACY_PLAYURL_URL = "https://api.bilibili.com/x/player/playurl"


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


def fetch_video_meta(bvid: str, cookie: str = "") -> tuple[dict | None, str | None]:
    data, err = request_bili_api_json(f"{VIEW_URL}?bvid={bvid}", cookie=cookie, timeout=BILI_API_TIMEOUT)
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


def _is_transient_bili_err(err: str) -> bool:
    text = (err or "").lower()
    return any(
        kw in text
        for kw in (
            "timeout", "timed out", "handshake", "connection reset",
            "connection refused", "curl: (28)", "curl: (35)", "ssl",
            "operation timed out", "transport", "连接 b 站", "不稳定",
        )
    )


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

    page_headers = {
        "Referer": f"https://www.bilibili.com/video/{bvid}",
        "Origin": "https://www.bilibili.com",
    }
    legacy_q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    def _parse_play_data(data: dict | None, err: str | None) -> tuple[dict | None, str | None]:
        if data is None:
            return None, err
        code = data.get("code")
        if code == 0:
            payload = data.get("data") or {}
            if payload.get("v_voucher"):
                return None, "B站风控验证(v_voucher)，请稍后重试"
            return payload, None
        return None, str(data.get("message") or f"playurl(code={code})")

    # legacy 无需 WBI nav，下载链路优先
    data, err = request_bili_api_json(
        f"{LEGACY_PLAYURL_URL}?{legacy_q}",
        cookie=cookie,
        timeout=BILI_API_TIMEOUT,
        extra_headers=page_headers,
    )
    ok, perr = _parse_play_data(data, err)
    if ok is not None:
        return ok, perr

    mixin_key, key_err = get_mixin_key(cookie=cookie)
    if not mixin_key:
        return None, key_err or perr or err or "WBI 密钥不可用"

    query = sign_wbi_query(params, mixin_key)
    data, err = request_bili_api_json(
        f"{PLAYURL_URL}?{query}",
        cookie=cookie,
        timeout=BILI_API_TIMEOUT,
        extra_headers=page_headers,
    )
    return _parse_play_data(data, err or perr)


def _curl_download(url: str, output_path: str, cookie: str, deadline: DownloadDeadline) -> tuple[bool, str]:
    if deadline.expired():
        return False, "下载超时"
    max_time = deadline.curl_timeout(120)
    cmd = [
        "curl", "-L", "-sS", "-4",
        *curl_no_proxy_args(),
        "--max-time", str(max_time),
        "-H", "Referer: https://www.bilibili.com/",
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ]
    if cookie:
        cmd.extend(["-H", f"Cookie: {cookie}"])
    cmd.extend(["-o", output_path, url])
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sanitize_env(),
        start_new_session=True,
    )
    try:
        _, stderr = proc.communicate(timeout=max_time + 10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        return False, "下载流超时"
    if proc.returncode != 0:
        return False, (stderr or "curl 失败").strip()[:200]
    if not os.path.exists(output_path) or os.path.getsize(output_path) < 50000:
        return False, "下载文件无效"
    return True, ""


def _merge_av(video_path: str, audio_path: str, output_path: str, deadline: DownloadDeadline) -> tuple[bool, str]:
    if deadline.expired():
        return False, "下载超时"
    ffmpeg = FFMPEG_PATH or "ffmpeg"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", video_path,
        "-i", audio_path,
        "-c", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        _, stderr = proc.communicate(timeout=deadline.curl_timeout(90))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        return False, "合并音视频超时"
    if proc.returncode != 0:
        return False, (stderr or "ffmpeg 合并失败")[:200]
    return True, ""


def _try_playurl_combo(
    meta: dict,
    cookie: str,
    output_path: str,
    deadline: DownloadDeadline,
    *,
    qn: int,
    fnval: int,
) -> tuple[bool, str, bool]:
    """返回 (ok, err, skip)。"""
    if deadline.expired():
        return False, "下载超时", False

    bvid = meta["bvid"]
    cid = meta.get("cid")
    aid = meta.get("aid")
    if not cid:
        return False, "缺少 cid", True

    play_data, perr = fetch_playurl(
        bvid=bvid,
        cid=int(cid),
        aid=int(aid) if aid else None,
        cookie=cookie,
        qn=qn,
        fnval=fnval,
    )
    if play_data is None:
        err = perr or "获取播放地址失败"
        skip = any(k in err for k in ("412", "权限", "版权", "地区限制", "404"))
        return False, err, skip

    video, audio, mode = _streams_from_play_data(play_data, max_height=720 if qn <= 64 else 1080)
    if video is None:
        return False, "无可用视频流", False

    video_url = _pick_url(video)
    if mode == "durl":
        ok, verr = _curl_download(video_url, output_path, cookie, deadline)
        if not ok:
            return False, verr, False
        if os.path.exists(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb > MAX_SIZE_MB:
                os.remove(output_path)
                return False, f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制", True
            return True, "", False
        return False, "下载文件无效", False

    tmp_dir = os.path.dirname(output_path) or "."
    video_tmp = os.path.join(tmp_dir, f".{bvid}_v.m4s")
    audio_tmp = os.path.join(tmp_dir, f".{bvid}_a.m4s")
    for path in (video_tmp, audio_tmp, output_path):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    ok, verr = _curl_download(video_url, video_tmp, cookie, deadline)
    if not ok:
        return False, verr, False

    size_mb = os.path.getsize(video_tmp) / (1024 * 1024)
    if size_mb > MAX_SIZE_MB:
        os.remove(video_tmp)
        return False, f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制", True

    if audio and _pick_url(audio):
        ok, aerr = _curl_download(_pick_url(audio), audio_tmp, cookie, deadline)
        if not ok:
            return False, aerr, False
        ok, merr = _merge_av(video_tmp, audio_tmp, output_path, deadline)
        for path in (video_tmp, audio_tmp):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        if not ok:
            return False, merr, False
    else:
        try:
            os.replace(video_tmp, output_path)
        except Exception as e:
            return False, str(e), False

    if os.path.exists(output_path):
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        if size_mb > MAX_SIZE_MB:
            os.remove(output_path)
            return False, f"视频约 {size_mb:.1f}MB 超过 {MAX_SIZE_MB}MB 限制", True
        return True, "", False
    return False, "API 下载失败", False


def download_bili_via_api(
    bvid: str,
    output_path: str,
    cookie: str = "",
    *,
    deadline_sec: float | None = None,
) -> tuple[bool, str, bool]:
    """通过 view + playurl API 下载，返回 (ok, err, skip)。"""
    deadline = DownloadDeadline(deadline_sec)
    meta, err = fetch_video_meta(bvid, cookie)
    if meta is None:
        return False, err or "获取视频信息失败", False

    ok, err, skip = _try_playurl_combo(meta, cookie, output_path, deadline, qn=80, fnval=4048)
    if ok:
        return True, "", False
    if skip:
        return False, err, True
    if _is_transient_bili_err(err or ""):
        return False, err, False
    if deadline.expired():
        return False, "下载超时", False
    ok, err, skip = _try_playurl_combo(meta, cookie, output_path, deadline, qn=64, fnval=16)
    if ok:
        return True, "", False
    return False, err or "API 下载失败", skip
