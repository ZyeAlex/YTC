"""FastAPI 鉴权依赖"""

from __future__ import annotations

from fastapi import Header, HTTPException

from backend.auth import verify_access_token


async def require_auth(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[7:].strip()
    if not verify_access_token(token):
        raise HTTPException(status_code=401, detail="Token 无效，请重新登录")
