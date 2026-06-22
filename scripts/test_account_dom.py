#!/usr/bin/env python3
"""模拟前端 renderDialogAccounts + getDialogAccounts 逻辑"""
from __future__ import annotations

import json
import re
import urllib.request
from html import escape


def fetch_accounts() -> list[dict]:
    with urllib.request.urlopen("http://127.0.0.1:8765/api/accounts") as r:
        return json.load(r)["accounts"]


def render_dialog_accounts(accounts: list[dict]) -> str:
    qq = [a for a in accounts if a["type"] == "qq"]
    bot = [a for a in accounts if a["type"] == "bot"]

    def render_group(group: list[dict], checked_ids: set[str] | None = None) -> str:
        checked_ids = checked_ids if checked_ids is not None else {a["id"] for a in group}
        return "".join(
            f'<label class="check-item">'
            f'<input type="checkbox" data-account="{escape(a["id"])}"'
            f'{" checked" if a["id"] in checked_ids else ""} />'
            f"<span>{escape(a['name'])}</span></label>"
            for a in group
        )

    qq_html = render_group(qq)
    bot_html = render_group(bot)
    return f"""
<div id="taskDialog">
  <div class="account-groups">
    <div class="account-group">
      <div id="dialogQqAccounts" class="check-grid compact">{qq_html}</div>
    </div>
    <div class="account-group">
      <div id="dialogBotAccounts" class="check-grid compact">{bot_html}</div>
    </div>
  </div>
</div>
"""


def get_dialog_accounts(html: str) -> list[str]:
    """等价于 #taskDialog .account-groups input:checked 的 data-account"""
    ids: list[str] = []
    for m in re.finditer(
        r'<input type="checkbox" data-account="([^"]+)"(\s+checked)?\s*/>',
        html,
    ):
        if m.group(2):
            in_groups = True
            # 仅统计 account-groups 区块内
            start = html.rfind('class="account-groups"', 0, m.start())
            end = html.find('</div>\n</div>', m.start())
            if start >= 0 and (end < 0 or m.start() < end):
                ids.append(m.group(1))
    return ids


def main() -> None:
    accounts = fetch_accounts()
    qq_ids = [a["id"] for a in accounts if a["type"] == "qq"]
    bot_ids = [a["id"] for a in accounts if a["type"] == "bot"]

    html = render_dialog_accounts(accounts)
    checked = get_dialog_accounts(html)
    assert checked == [a["id"] for a in accounts], checked
    print(f"PASS default all checked ({len(checked)})")

    html_bot = render_dialog_accounts(accounts, )  # need custom render
    # rebuild with only bot checked
    html2 = render_dialog_accounts_with(accounts, set(bot_ids))
    checked2 = get_dialog_accounts(html2)
    assert set(checked2) == set(bot_ids) and not set(qq_ids) & set(checked2)
    print(f"PASS deselect QQ -> bot only ({len(checked2)})")

    html3 = render_dialog_accounts_with(accounts, set())
    checked3 = get_dialog_accounts(html3)
    assert checked3 == []
    print("PASS deselect all -> empty []")

    html4 = render_dialog_accounts_with(accounts, {"qq:1"})
    checked4 = get_dialog_accounts(html4)
    assert checked4 == ["qq:1"]
    print("PASS single qq:1 only")

    # API 回环：提交 bot 子集
    import urllib.request as u
    ch = json.load(u.urlopen("http://127.0.0.1:8765/api/channels"))["channels"][0]
    body = {
        "platform": "bili",
        "keyword": "acc-test",
        "videos": [{"id": "BVacc001", "title": "t", "platform": "bili"}],
        "channels": [{"guild_id": ch["guild_id"], "channel_id": ch["channel_id"], "name": ch["name"]}],
        "account_ids": bot_ids[:2],
        "schedule_cron": "0,20,40 * * * *",
    }
    req = u.Request(
        "http://127.0.0.1:8765/api/tasks",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = json.load(u.urlopen(req))
    detail = json.load(u.urlopen(f"http://127.0.0.1:8765/api/tasks/{resp['task_id']}"))
    assert detail["account_ids"] == bot_ids[:2]
    u.urlopen(u.Request(f"http://127.0.0.1:8765/api/tasks/{resp['task_id']}", method="DELETE"))
    print(f"PASS API stores selected subset {detail['account_ids']}")


def render_dialog_accounts_with(accounts: list[dict], checked: set[str]) -> str:
    qq = [a for a in accounts if a["type"] == "qq"]
    bot = [a for a in accounts if a["type"] == "bot"]

    def render_group(group: list[dict]) -> str:
        return "".join(
            f'<label class="check-item">'
            f'<input type="checkbox" data-account="{escape(a["id"])}"'
            f'{" checked" if a["id"] in checked else ""} />'
            f"<span>{escape(a['name'])}</span></label>"
            for a in group
        )

    return f"""
<div id="taskDialog">
  <div class="account-groups">
    <div class="account-group">
      <div id="dialogQqAccounts" class="check-grid compact">{render_group(qq)}</div>
    </div>
    <div class="account-group">
      <div id="dialogBotAccounts" class="check-grid compact">{render_group(bot)}</div>
    </div>
  </div>
</div>
"""


if __name__ == "__main__":
    main()
