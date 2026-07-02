from __future__ import annotations

import json
import os
import re
import subprocess
import time

from backend.config import DOUYIN_SEARCH_JS, NODE_PATH, DOUYIN_SKILL_DIR
from backend.data.app_config import get_guaikei_token
from backend.services.proxy_bypass import direct_connect_env

# guaikei API 单次实际最多约 20 条（日志中从未成功超过 20）
DOUYIN_API_LIMIT = 20
DOUYIN_SEARCH_TIMEOUT = 120
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", s)


def _clean_keyword(keyword: str) -> str:
    keyword = keyword.strip()
    keyword = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s.,!?# ，。！？]", "", keyword)
    return re.sub(r"\s+", " ", keyword)


def _extract_json_objects(raw: str) -> list[dict]:
    objects: list[dict] = []
    start = 0
    while True:
        start = raw.find("{", start)
        if start == -1:
            break
        depth = 0
        for i in range(start, len(raw)):
            ch = raw[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    snippet = raw[start : i + 1]
                    try:
                        data = json.loads(snippet)
                        if isinstance(data, dict):
                            objects.append(data)
                    except json.JSONDecodeError:
                        pass
                    start = i + 1
                    break
        else:
            break
    return objects


def _parse_cli_response(stdout: str) -> dict:
    """解析 CLI 输出，返回 results / status / message。"""
    raw = _strip_ansi(stdout or "").strip()
    empty = {"results": [], "status": None, "message": ""}
    if not raw:
        return empty

    candidates = _extract_json_objects(raw)
    for data in reversed(candidates):
        items = data.get("results", data.get("videos", data.get("data", [])))
        if not isinstance(items, list):
            items = []
        status = data.get("status")
        message = data.get("message") or data.get("error") or ""
        if items or status in ("error", "empty") or message:
            return {"results": items, "status": status, "message": message}
    return empty


def _find_latest_log_json(keyword: str, sort: int, limit: int) -> str | None:
    log_dir = DOUYIN_SKILL_DIR / "logs"
    if not log_dir.is_dir():
        return None

    cleaned = _clean_keyword(keyword)
    pattern = re.compile(rf"^\d+_{re.escape(cleaned)}_{sort}_\d+_search\.json$")
    candidates: list[tuple[int, str]] = []

    for fn in os.listdir(log_dir):
        if not pattern.match(fn):
            continue
        path = log_dir / fn
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("limit") != limit:
            continue
        if data.get("sort") != sort:
            continue
        try:
            ts = int(fn.split("_", 1)[0])
        except ValueError:
            ts = int(path.stat().st_mtime * 1000)
        candidates.append((ts, str(path)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _load_log_results(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    items = data.get("results", data.get("videos", data.get("data", [])))
    return items if isinstance(items, list) else []


def _validate_guaikei_token(token: str) -> str | None:
    token = (token or "").strip()
    if not token:
        return "GUAIKEI API Token 未配置，请在 config/config.json 填写 guaikei_api_token"
    if not _TOKEN_RE.match(token):
        return "GUAIKEI API Token 格式无效（应为 32 位十六进制）"
    return None


def search_douyin(keyword: str, sort: int = 2, limit: int = DOUYIN_API_LIMIT) -> dict:
    """
    sort: 0=综合, 1=最多点赞, 2=最新发布
    time 固定为 0（全部时间），与 CLI 默认一致
    """
    limit = max(1, min(int(limit), DOUYIN_API_LIMIT))
    if not DOUYIN_SEARCH_JS.exists():
        return {
            "error": "抖音搜索组件未安装，请确认 skills/douyin-search-keyword 存在",
            "videos": [],
        }

    token = get_guaikei_token()
    token_err = _validate_guaikei_token(token)
    if token_err:
        return {"error": token_err, "videos": []}

    node = NODE_PATH
    cmd = [
        node, str(DOUYIN_SEARCH_JS),
        "--keyword", keyword,
        "--sort", str(sort),
        "--time", "0",
        "--limit", str(limit),
        "--output", "json",
    ]
    env = direct_connect_env()
    env["GUAIKEI_API_TOKEN"] = token

    started_at = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DOUYIN_SEARCH_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return {
            "error": f"抖音搜索超时（超过 {DOUYIN_SEARCH_TIMEOUT} 秒），请检查 GUAIKEI Token 或稍后重试",
            "videos": [],
        }
    except Exception as e:
        return {"error": str(e), "videos": []}

    parsed = _parse_cli_response(result.stdout)
    raw_list = parsed["results"]
    source = "stdout"
    api_message = (parsed.get("message") or "").strip()
    api_status = parsed.get("status")

    if not raw_list:
        log_path = _find_latest_log_json(keyword, sort, limit)
        if log_path and os.path.getmtime(log_path) >= started_at - 5:
            raw_list = _load_log_results(log_path)
            source = "log"

    if not raw_list:
        if api_status == "error" and api_message:
            return {"error": api_message, "videos": []}
        if api_status == "empty" and api_message:
            return {"videos": [], "warning": api_message, "raw_count": 0, "requested_limit": limit}
        if result.returncode != 0:
            err = _strip_ansi(result.stderr or result.stdout).strip()
            return {"error": err or api_message or "抖音搜索失败", "videos": []}

    videos = []
    skipped = 0
    for v in raw_list or []:
        if _is_image_only(v):
            skipped += 1
            continue
        nv = _normalize(v)
        if nv:
            videos.append(nv)

    out: dict = {
        "videos": videos,
        "total": len(videos),
        "requested_limit": limit,
        "raw_count": len(raw_list or []),
        "source": source,
    }
    if skipped:
        out["warning"] = f"已过滤 {skipped} 条图文帖（无视频）"
    if not videos and not out.get("warning") and raw_list:
        out["warning"] = "未找到可下载的视频"
    if api_message and api_status == "success" and len(raw_list or []) < limit:
        hint = f"抖音 API 返回 {len(raw_list or [])} 条（请求 {limit} 条）"
        out["warning"] = f"{out['warning']}；{hint}" if out.get("warning") else hint
    return out


def _is_image_only(v: dict) -> bool:
    """图文帖（多图笔记）没有可下载视频"""
    if v.get("play_addr"):
        return False
    if v.get("images"):
        return True
    share_url = v.get("share_url") or v.get("url") or ""
    return "/note/" in share_url


def _normalize(v: dict) -> dict | None:
    video_id = str(v.get("aweme_id") or v.get("id") or "")
    if not video_id:
        return None
    title = v.get("desc") or v.get("title") or ""
    author = v.get("author_nickname") or ""
    if not author and isinstance(v.get("author"), dict):
        author = v["author"].get("nickname") or v["author"].get("unique_id") or ""
    share_url = v.get("share_url") or v.get("url") or f"https://www.douyin.com/video/{video_id}"
    play_addr = v.get("play_addr") or ""
    if not play_addr and isinstance(v.get("video"), dict):
        play_addr = v["video"].get("play_addr", "")
    pic = v.get("cover") or v.get("cover_url") or ""
    if not pic and isinstance(v.get("dynamic_cover"), list) and v["dynamic_cover"]:
        pic = v["dynamic_cover"][0]
    if not pic and isinstance(v.get("video"), dict):
        pic = v["video"].get("cover", {}).get("url_list", [""])[0] if isinstance(v["video"].get("cover"), dict) else ""
    return {
        "id": video_id,
        "title": title,
        "author": author,
        "link": share_url,
        "pic": pic,
        "play_addr": play_addr,
        "digg_count": v.get("digg_count", 0),
        "platform": "douyin",
    }
