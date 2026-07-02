#!/usr/bin/env python3
"""本地测试 B 站搜索（读取 config/config.json 中的 Cookie，不打印 Cookie 内容）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.app_config import get_bili_cookies
from backend.services.bili_wbi import NAV_URL, get_mixin_key
from backend.services.bili_wbi import _request_json_url
from backend.services.search_bili import search_bili


def main() -> int:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "蓝色星原"
    cookies = get_bili_cookies()
    print(f"关键词: {keyword}")
    print(f"已配置 Cookie 数量: {len(cookies)}")

    cookie = cookies[0] if cookies else ""
    nav, nav_err = _request_json_url(NAV_URL, cookie=cookie)
    print("nav:", "OK" if nav and nav.get("code") == 0 else (nav_err or nav))

    mk, mk_err = get_mixin_key(force_refresh=True, cookie=cookie)
    print("wbi:", "OK" if mk else (mk_err or "失败"))

    result = search_bili(keyword, order="pubdate", pages=1)
    if result.get("error") and not result.get("videos"):
        print("搜索失败:", result["error"])
        return 1

    videos = result.get("videos") or []
    print(f"搜索成功: {len(videos)} 条")
    for v in videos[:5]:
        print(f"  - {v.get('id')} | {v.get('title', '')[:50]}")
    if result.get("warning"):
        print("warning:", result["warning"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
