"""登录鉴权：config.json 中的 access_token（明文比对）"""

from __future__ import annotations

from backend.data.app_config import get_access_token, init_storage, save_access_token


def init_auth() -> None:
    init_storage()


def is_token_configured() -> bool:
    return bool(get_access_token())


def verify_access_token(token: str | None) -> bool:
    configured = get_access_token()
    if not configured:
        return False
    return (token or "").strip() == configured


def login_with_token(login_token: str) -> dict | None:
    raw = login_token.strip()
    if not raw:
        return None

    configured = get_access_token()
    if not configured:
        save_access_token(raw)
        return {
            "access_token": raw,
            "token_type": "bearer",
            "ok": True,
            "initial_setup": True,
        }

    if raw != configured:
        return None
    return {
        "access_token": raw,
        "token_type": "bearer",
        "ok": True,
        "initial_setup": False,
    }


def logout(_token: str | None) -> None:
    return None
