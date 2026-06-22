"""登录鉴权：config.json 中的 access_token（明文比对）"""

from __future__ import annotations

from backend.data.app_config import get_access_token, init_storage


def init_auth() -> None:
    init_storage()


def verify_access_token(token: str | None) -> bool:
    configured = get_access_token()
    if not configured:
        return False
    return (token or "").strip() == configured


def login_with_token(login_token: str) -> dict | None:
    raw = login_token.strip()
    if not verify_access_token(raw):
        return None
    return {
        "access_token": raw,
        "token_type": "bearer",
        "ok": True,
    }


def logout(_token: str | None) -> None:
    return None
