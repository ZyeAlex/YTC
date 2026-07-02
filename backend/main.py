from pathlib import Path
from contextlib import asynccontextmanager
import asyncio

import backend.services.proxy_bypass  # noqa: F401 — 启动时剥离进程代理

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.auth import init_auth, login_with_token, logout, verify_access_token
from backend.auto_like_scheduler import (
    get_auto_like_status,
    run_channel_now,
    start_auto_like_scheduler,
    stop_auto_like_scheduler,
)
from backend.data.account_alerts import clear_alerts, list_alerts
from backend.data.accounts import list_accounts_full, list_accounts_public
from backend.data.auto_like_tasks import (
    get_auto_like_config,
    save_auto_like_config,
    upsert_auto_like_channel,
)
from backend.data.channels import get_channels, save_channel_order
from backend.data.filter_patterns import get_filter_patterns, save_filter_patterns
from backend.data.app_config import (
    get_bili_cookies,
    get_douyin_cookies,
    save_bili_cookies,
    save_douyin_cookies,
)
from backend.data.user_data import get_accounts, get_channels as get_user_channels
from backend.data.user_data import save_accounts, save_channels as save_user_channels
from backend.deps import require_auth
from backend.models import (
    AddAccountRequest,
    AddChannelFromShareRequest,
    AutoLikeChannelConfig,
    AutoLikeConfigRequest,
    ChannelReorderRequest,
    FilterPatternsRequest,
    LoginRequest,
    CookieListUpdate,
    SchedulePreviewRequest,
    SearchRequest,
    TaskCreateRequest,
    TaskUpdateRequest,
    DouyinLinksRequest,
    DouyinCollectionRequest,
    DouyinCollectsListRequest,
    UserAccountsUpdate,
    UserChannelsUpdate,
)
from backend.schedule import describe_cron, format_next_time, seconds_until_next, validate_cron
from backend.services.add_account import add_account, normalize_account_entries, stream_add_account_sse_async
from backend.services.join_channel import add_channel_from_share
from backend.services.search_service import search_videos
from backend.services.video_filter import DEFAULT_FILTER_PATTERNS
from backend.tasks import (
    create_task,
    delete_task,
    get_task,
    get_task_detail,
    list_tasks,
    pause_task,
    append_custom_videos,
    sync_collection_videos,
    send_task_video,
    start_task,
    update_task,
    start_background_tasks,
    stop_background_tasks,
)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_auth()
    start_background_tasks()
    start_auto_like_scheduler()
    yield
    await stop_auto_like_scheduler()
    await stop_background_tasks()


app = FastAPI(title="腾讯频道发帖工具", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/auth/login")
def api_login(req: LoginRequest):
    result = login_with_token(req.token.strip())
    if not result:
        raise HTTPException(401, "Token 无效")
    return result


@app.get("/api/auth/me")
def api_me(authorization: str | None = Header(default=None)):
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not verify_access_token(token):
        raise HTTPException(401, "未登录")
    return {"ok": True}


@app.post("/api/auth/logout")
def api_logout(authorization: str | None = Header(default=None)):
    if authorization and authorization.lower().startswith("bearer "):
        logout(authorization[7:].strip())
    return {"ok": True}


@app.get("/api/settings/profile")
def api_get_profile(_auth: None = Depends(require_auth)):
    return {
        "accounts": list_accounts_full(),
        "channels": get_user_channels(),
        "bili_cookies": get_bili_cookies(),
        "douyin_cookies": get_douyin_cookies(),
    }


@app.put("/api/settings/cookies/bili")
def api_save_bili_cookies(req: CookieListUpdate, _auth: None = Depends(require_auth)):
    return {
        "ok": True,
        "bili_cookies": save_bili_cookies(req.cookies),
        "douyin_cookies": get_douyin_cookies(),
    }


@app.put("/api/settings/cookies/douyin")
def api_save_douyin_cookies(req: CookieListUpdate, _auth: None = Depends(require_auth)):
    return {
        "ok": True,
        "bili_cookies": get_bili_cookies(),
        "douyin_cookies": save_douyin_cookies(req.cookies),
    }


@app.put("/api/settings/accounts")
def api_save_accounts(req: UserAccountsUpdate, _auth: None = Depends(require_auth)):
    saved = save_accounts({
        "qq_accounts": [a.model_dump() for a in req.qq_accounts],
        "bot_accounts": [a.model_dump() for a in req.bot_accounts],
    })
    return {"ok": True, "accounts": saved}


@app.post("/api/accounts/add")
def api_add_account(req: AddAccountRequest, _auth: None = Depends(require_auth)):
    try:
        entries = normalize_account_entries([a.model_dump() for a in req.accounts])
        return add_account(entries)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/accounts/add-stream")
async def api_add_account_stream(req: AddAccountRequest, _auth: None = Depends(require_auth)):
    try:
        entries = normalize_account_entries([a.model_dump() for a in req.accounts])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return StreamingResponse(
        stream_add_account_sse_async(entries),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.put("/api/settings/channels")
def api_save_channels(req: UserChannelsUpdate, _auth: None = Depends(require_auth)):
    saved = save_user_channels([c.model_dump() for c in req.channels])
    return {"ok": True, "channels": saved}


@app.post("/api/channels/add-from-share")
def api_add_channel_from_share(req: AddChannelFromShareRequest, _auth: None = Depends(require_auth)):
    try:
        return add_channel_from_share(req.text, req.category)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/channels")
def api_channels(_auth: None = Depends(require_auth)):
    return {"channels": get_channels()}


@app.put("/api/channels/order")
def api_channels_order(req: ChannelReorderRequest, _auth: None = Depends(require_auth)):
    if not req.order:
        raise HTTPException(400, "排序列表不能为空")
    ordered = save_channel_order([item.model_dump() for item in req.order])
    return {"ok": True, "channels": ordered}


@app.get("/api/accounts")
def api_accounts(_auth: None = Depends(require_auth)):
    return {"accounts": list_accounts_public()}


@app.get("/api/filter-patterns")
def api_get_filter_patterns(_auth: None = Depends(require_auth)):
    patterns = get_filter_patterns()
    return {"patterns": patterns, "defaults": DEFAULT_FILTER_PATTERNS, "count": len(patterns)}


@app.put("/api/filter-patterns")
def api_save_filter_patterns(req: FilterPatternsRequest, _auth: None = Depends(require_auth)):
    patterns = save_filter_patterns(req.patterns)
    return {"ok": True, "patterns": patterns, "count": len(patterns)}


@app.get("/api/system-alerts")
def api_list_system_alerts(_auth: None = Depends(require_auth)):
    alerts = list_alerts(limit=200)
    return {"alerts": alerts, "count": len(alerts)}


@app.delete("/api/system-alerts")
def api_clear_system_alerts(_auth: None = Depends(require_auth)):
    clear_alerts()
    return {"ok": True}


@app.get("/api/settings/auto-like")
def api_get_auto_like(_auth: None = Depends(require_auth)):
    cfg = get_auto_like_config()
    status = get_auto_like_status()
    return {"config": cfg, "status": status}


@app.put("/api/settings/auto-like")
def api_save_auto_like(req: AutoLikeConfigRequest, _auth: None = Depends(require_auth)):
    channels = []
    for ch in req.channels:
        item = ch.model_dump()
        cron = (item.get("schedule_cron") or "").strip()
        if item.get("likes_max", 0) < item.get("likes_min", 1):
            item["likes_max"] = item["likes_min"]
        if cron:
            _, next_ts = seconds_until_next(cron)
            item["next_run_at"] = next_ts
        channels.append(item)
    saved = save_auto_like_config({"enabled": True, "channels": channels})
    return {"ok": True, "config": saved}


@app.put("/api/settings/auto-like/channel/{guild_id}/{channel_id}")
def api_save_auto_like_channel(
    guild_id: str,
    channel_id: str,
    req: AutoLikeChannelConfig,
    _auth: None = Depends(require_auth),
):
    if req.guild_id != guild_id or req.channel_id != channel_id:
        raise HTTPException(400, "路径与请求体频道 ID 不一致")
    saved = upsert_auto_like_channel(req.model_dump())
    ch = next(
        (
            c for c in saved.get("channels", [])
            if c.get("guild_id") == guild_id and c.get("channel_id") == channel_id
        ),
        None,
    )
    return {"ok": True, "channel": ch, "config": saved}


@app.post("/api/settings/auto-like/run/{guild_id}/{channel_id}")
async def api_run_auto_like_now(
    guild_id: str,
    channel_id: str,
    _auth: None = Depends(require_auth),
):
    try:
        return await run_channel_now(guild_id, channel_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/search")
def api_search(req: SearchRequest, _auth: None = Depends(require_auth)):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(400, "关键词不能为空")

    result = search_videos(req.platform, keyword, bili_pages=req.bili_pages, search_sort=req.search_sort)
    if result.get("error") and not result.get("videos"):
        raise HTTPException(502, result["error"])

    return {
        "platform": req.platform,
        "keyword": keyword,
        "videos": result.get("videos", []),
        "total": result.get("total", 0),
        "filtered_count": result.get("filtered_count", 0),
        "raw_count": result.get("raw_count"),
        "requested_limit": result.get("requested_limit"),
        "pages_fetched": result.get("pages_fetched"),
        "pages_requested": result.get("pages_requested"),
        "pattern_errors": result.get("pattern_errors", []),
        "search_sort": result.get("search_sort"),
        "warning": result.get("warning") or None,
    }


@app.post("/api/schedule/preview")
def api_schedule_preview(req: SchedulePreviewRequest, _auth: None = Depends(require_auth)):
    cron = req.schedule_cron.strip()
    if not validate_cron(cron):
        raise HTTPException(400, "Cron 表达式无效")
    wait_sec, next_ts = seconds_until_next(cron)
    return {
        "schedule_cron": cron,
        "schedule_desc": describe_cron(cron),
        "next_time": format_next_time(next_ts),
        "next_in_seconds": wait_sec,
    }


@app.get("/api/tasks")
def api_list_tasks(_auth: None = Depends(require_auth)):
    return {"tasks": list_tasks()}


@app.post("/api/tasks")
async def api_create_task(req: TaskCreateRequest, _auth: None = Depends(require_auth)):
    if not req.channels:
        raise HTTPException(400, "请至少选择一个频道")
    if not req.account_ids:
        raise HTTPException(400, "请至少选择一个发送账号")
    if not req.videos and req.task_type != "custom":
        raise HTTPException(400, "没有待发送的视频")
    if (
        req.task_type == "recurring"
        and not req.keyword.strip()
        and req.source != "collection"
    ):
        raise HTTPException(400, "长期任务需要填写搜索关键词")
    if req.task_type == "custom" and req.platform != "douyin":
        raise HTTPException(400, "自定义任务仅支持抖音")
    if req.source == "collection" and req.platform != "douyin":
        raise HTTPException(400, "收藏夹任务仅支持抖音")

    account_ids = req.account_ids
    payload = {
        "task_type": req.task_type,
        "platform": "douyin" if req.task_type == "custom" else req.platform,
        "keyword": req.keyword.strip(),
        "videos": [v.model_dump() for v in req.videos],
        "channels": [c.model_dump() for c in req.channels],
        "account_ids": account_ids,
        "schedule_cron": req.schedule_cron.strip(),
        "search_sort": req.search_sort,
        "batch_count": 1 if req.task_type == "recurring" else 0,
        "source": req.source,
        "douyin_cookie_index": req.douyin_cookie_index,
        "douyin_collects_id": req.douyin_collects_id.strip(),
        "collection_account_label": req.collection_account_label.strip(),
        "include_topics": req.include_topics,
    }
    try:
        task_id = create_task(
            payload,
            name=req.name.strip(),
            keyword=req.keyword.strip(),
            auto_start=req.auto_start,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    task = get_task(task_id)
    return {"task_id": task_id, "task": {
        "task_id": task.task_id,
        "name": task.name,
        "status": task.status,
        "platform": task.platform,
        "keyword": task.keyword,
        "video_count": task.video_count,
        "channel_count": task.channel_count,
        "created_at": task.created_at,
    }}


@app.put("/api/tasks/{task_id}")
def api_update_task(task_id: str, req: TaskUpdateRequest, _auth: None = Depends(require_auth)):
    if not get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not req.channels:
        raise HTTPException(400, "请至少选择一个频道")
    if not req.account_ids:
        raise HTTPException(400, "请至少选择一个发送账号")
    if (
        req.task_type == "recurring"
        and not req.keyword.strip()
        and req.source != "collection"
    ):
        raise HTTPException(400, "长期任务需要填写搜索关键词")
    if req.task_type == "custom" and req.platform != "douyin":
        raise HTTPException(400, "自定义任务仅支持抖音")
    if req.source == "collection" and req.platform != "douyin":
        raise HTTPException(400, "收藏夹任务仅支持抖音")

    payload = {
        "task_type": req.task_type,
        "platform": "douyin" if req.task_type == "custom" else req.platform,
        "keyword": req.keyword.strip(),
        "videos": [v.model_dump() for v in req.videos],
        "channels": [c.model_dump() for c in req.channels],
        "account_ids": req.account_ids,
        "schedule_cron": req.schedule_cron.strip(),
        "search_sort": req.search_sort,
        "source": req.source,
        "douyin_cookie_index": req.douyin_cookie_index,
        "douyin_collects_id": req.douyin_collects_id.strip(),
        "collection_account_label": req.collection_account_label.strip(),
        "include_topics": req.include_topics,
    }
    try:
        update_task(task_id, payload, keyword=req.keyword.strip())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "task": get_task_detail(task_id)}


@app.post("/api/tasks/{task_id}/start")
async def api_start_task(task_id: str, _auth: None = Depends(require_auth)):
    if not get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not start_task(task_id):
        raise HTTPException(400, "任务无法启动（可能已在运行或已完成）")
    task = get_task(task_id)
    return {"ok": True, "status": task.status}


@app.post("/api/tasks/{task_id}/pause")
async def api_pause_task(task_id: str, _auth: None = Depends(require_auth)):
    if not get_task(task_id):
        raise HTTPException(404, "任务不存在")
    if not pause_task(task_id):
        raise HTTPException(400, "任务无法暂停（可能未在运行）")
    return {"ok": True, "status": "paused"}


@app.get("/api/douyin/cookie-accounts")
def api_douyin_cookie_accounts(_auth: None = Depends(require_auth)):
    from backend.services.douyin_collection import get_douyin_cookie_accounts

    return {"accounts": get_douyin_cookie_accounts()}


@app.post("/api/douyin/collects-list")
def api_douyin_collects_list(req: DouyinCollectsListRequest, _auth: None = Depends(require_auth)):
    from backend.services.douyin_collection import fetch_douyin_collects_list

    return fetch_douyin_collects_list(cookie_index=req.cookie_index)


@app.post("/api/douyin/collection")
def api_douyin_collection(req: DouyinCollectionRequest, _auth: None = Depends(require_auth)):
    if req.fetch_all:
        from backend.services.douyin_collection import fetch_douyin_collection_all

        result = fetch_douyin_collection_all(
            cookie_index=req.cookie_index,
            collects_id=req.collects_id.strip(),
            max_items=req.max_items,
            page_size=req.count,
            page_delay=1.2,
        )
    else:
        from backend.services.douyin_collection import fetch_douyin_collection

        result = fetch_douyin_collection(
            cookie_index=req.cookie_index,
            cursor=req.cursor,
            count=req.count,
            collects_id=req.collects_id.strip(),
        )
    if result.get("error") and not result.get("videos"):
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/douyin/parse-links")
def api_parse_douyin_links(req: DouyinLinksRequest, _auth: None = Depends(require_auth)):
    from backend.services.parse_douyin_links import parse_douyin_link_list

    result = parse_douyin_link_list(req.text)
    return result


@app.post("/api/tasks/{task_id}/append-links")
def api_append_task_links(task_id: str, req: DouyinLinksRequest, _auth: None = Depends(require_auth)):
    try:
        return append_custom_videos(task_id, req.text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/tasks/{task_id}/sync-collection")
async def api_sync_task_collection(task_id: str, _auth: None = Depends(require_auth)):
    try:
        result = await asyncio.to_thread(sync_collection_videos, task_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"同步异常: {e}") from e

    resume_error = ""
    if result.get("should_resume"):
        try:
            if not start_task(task_id):
                resume_error = "视频已同步，但任务未能自动继续（请手动点击继续执行）"
        except Exception as e:
            resume_error = f"视频已同步，自动继续失败: {e}"
    if resume_error:
        result["resume_error"] = resume_error
        result["warning"] = (result.get("warning") or "") + ("; " if result.get("warning") else "") + resume_error
    return result


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str, _auth: None = Depends(require_auth)):
    delete_task(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/videos/{video_id}/send")
async def api_send_task_video(task_id: str, video_id: str, _auth: None = Depends(require_auth)):
    if not get_task(task_id):
        raise HTTPException(404, "任务不存在")
    try:
        return await send_task_video(task_id, video_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/tasks/{task_id}")
def api_task_status(task_id: str, _auth: None = Depends(require_auth)):
    detail = get_task_detail(task_id)
    if not detail:
        raise HTTPException(404, "任务不存在")
    return detail


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE)


@app.get("/settings")
def settings_page():
    return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE)


@app.get("/auto-like")
def auto_like_page():
    return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE)


@app.get("/alerts")
def alerts_page():
    return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
