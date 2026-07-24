from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    platform: str = Field(..., pattern="^(bili|douyin)$")
    keyword: str = Field(..., min_length=1)
    bili_pages: int = Field(default=1, ge=1, le=10)
    search_sort: str = Field(default="recent", pattern="^(default|recent)$")


class FilterPatternsRequest(BaseModel):
    patterns: list[str] = Field(default_factory=list)


class ChannelRef(BaseModel):
    guild_id: str
    channel_id: str
    name: str = ""


class ChannelOrderItem(BaseModel):
    guild_id: str
    channel_id: str


class ChannelReorderRequest(BaseModel):
    order: list[ChannelOrderItem]


class VideoRef(BaseModel):
    id: str
    title: str
    link: str = ""
    play_addr: str = ""
    platform: str = ""
    pic: str = ""
    author: str = ""


class DouyinLinksRequest(BaseModel):
    text: str = Field(..., min_length=1)


class DouyinCollectionRequest(BaseModel):
    cookie_index: int = Field(default=0, ge=0)
    collects_id: str = ""
    cursor: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=20)
    fetch_all: bool = False
    max_items: int = Field(default=2000, ge=1, le=5000)


class DouyinCollectsListRequest(BaseModel):
    cookie_index: int = Field(default=0, ge=0)

class TaskUpdateRequest(BaseModel):
    platform: str = Field(..., pattern="^(bili|douyin)$")
    keyword: str = ""
    task_type: str = Field(..., pattern="^(once|recurring|custom)$")
    videos: list[VideoRef] = Field(..., min_length=1)
    channels: list[ChannelRef]
    account_types: list[str] = Field(default_factory=list)  # qq / bot
    account_ids: list[str] = Field(default_factory=list)  # 兼容旧客户端
    schedule_cron: str = Field(default="")
    search_sort: str = Field(default="recent", pattern="^(default|recent)$")
    source: str = Field(default="search", pattern="^(search|collection|manual)$")
    douyin_cookie_index: int = Field(default=0, ge=0)
    douyin_collects_id: str = ""
    collection_account_label: str = ""
    include_topics: bool = True


class TaskCreateRequest(BaseModel):
    platform: str = Field(..., pattern="^(bili|douyin)$")
    keyword: str = ""
    name: str = ""
    task_type: str = Field(default="once", pattern="^(once|recurring|custom)$")
    videos: list[VideoRef] = Field(default_factory=list)
    channels: list[ChannelRef]
    account_types: list[str] = Field(default_factory=list)  # qq / bot
    account_ids: list[str] = Field(default_factory=list)  # 兼容旧客户端
    schedule_cron: str = Field(default="")
    search_sort: str = Field(default="recent", pattern="^(default|recent)$")
    source: str = Field(default="search", pattern="^(search|collection|manual)$")
    douyin_cookie_index: int = Field(default=0, ge=0)
    douyin_collects_id: str = ""
    collection_account_label: str = ""
    include_topics: bool = True
    auto_start: bool = False


class SchedulePreviewRequest(BaseModel):
    schedule_cron: str = Field(..., min_length=1)


class AutoLikeChannelConfig(BaseModel):
    guild_id: str
    channel_id: str
    name: str = ""
    enabled: bool = False
    likes_min: int = Field(default=1, ge=1)
    likes_max: int = Field(default=5, ge=1)
    schedule_cron: str = Field(default="")
    only_own_posts: bool = True
    account_ids: list[str] = Field(default_factory=list)
    feeds_per_channel: int = Field(default=20, ge=1, le=50)


class AutoLikeConfigRequest(BaseModel):
    enabled: bool = False
    channels: list[AutoLikeChannelConfig] = Field(default_factory=list)


class LoginRequest(BaseModel):
    token: str = Field(..., min_length=1)


class AccountItem(BaseModel):
    index: int = Field(..., ge=1)
    name: str = ""
    token: str = ""


class UserAccountsUpdate(BaseModel):
    qq_accounts: list[AccountItem] = Field(default_factory=list)
    bot_accounts: list[AccountItem] = Field(default_factory=list)


class UserChannelItem(BaseModel):
    name: str = ""
    guild_id: str
    channel_id: str
    category: str = "游戏"


class UserChannelsUpdate(BaseModel):
    channels: list[UserChannelItem] = Field(default_factory=list)


class AddChannelFromShareRequest(BaseModel):
    text: str = Field(..., min_length=1)
    category: str = Field(default="游戏")


class CookieListUpdate(BaseModel):
    cookies: list[str] = Field(default_factory=list)


class AddAccountEntry(BaseModel):
    name: str = ""
    token: str = Field(..., min_length=1)
    account_type: str = Field(default="bot", pattern="^(qq|bot)$")


class AddAccountRequest(BaseModel):
    accounts: list[AddAccountEntry] = Field(..., min_length=1)

