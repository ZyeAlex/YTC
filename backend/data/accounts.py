from __future__ import annotations

from backend.data.user_data import get_accounts as _get_accounts


def load_accounts() -> dict:
    return _get_accounts()


def list_accounts_public() -> list[dict]:
    """返回不含 token 的账号列表，供前端展示"""
    data = load_accounts()
    result = []
    for acc in data.get("qq_accounts", []):
        result.append({
            "id": f"qq:{acc['index']}",
            "type": "qq",
            "index": acc["index"],
            "global_index": acc["index"],
            "name": acc.get("name", f"QQ-{acc['index']}"),
        })
    qq_count = len(data.get("qq_accounts", []))
    for acc in data.get("bot_accounts", []):
        result.append({
            "id": f"bot:{acc['index']}",
            "type": "bot",
            "index": acc["index"],
            "global_index": qq_count + acc["index"],
            "name": acc.get("name", f"Bot-{acc['index']}"),
        })
    return result


def list_accounts_full() -> dict:
    return _get_accounts()


def get_token(account_id: str) -> str | None:
    """account_id 格式: qq:3 或 bot:5"""
    data = load_accounts()
    try:
        kind, idx_str = account_id.split(":", 1)
        idx = int(idx_str)
    except (ValueError, AttributeError):
        return None

    if kind == "qq":
        for acc in data.get("qq_accounts", []):
            if acc["index"] == idx:
                return acc["token"]
    elif kind == "bot":
        for acc in data.get("bot_accounts", []):
            if acc["index"] == idx:
                return acc["token"]
    return None


def get_account_label(account_id: str) -> str:
    """返回带类型的账号标签，如「QQ·栋华 (qq:1)」"""
    for acc in list_accounts_public():
        if acc["id"] == account_id:
            kind = "QQ" if acc["type"] == "qq" else "Bot"
            return f"{kind}·{acc['name']} ({account_id})"
    return account_id
