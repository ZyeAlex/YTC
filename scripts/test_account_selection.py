#!/usr/bin/env python3
"""浏览器测试：新建任务弹窗 account_ids 勾选逻辑"""
from __future__ import annotations

import json
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"


def get_checked_ids(page):
    return page.eval_on_selector_all(
        "#taskDialog .account-groups input:checked",
        "els => els.map(el => el.dataset.account)",
    )


def main() -> int:
    captured: dict | None = None
    fails = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_request(request):
            nonlocal captured
            if request.url.endswith("/api/tasks") and request.method == "POST":
                captured = json.loads(request.post_data or "{}")

        page.on("request", on_request)
        page.goto(BASE, wait_until="networkidle")
        page.click("#newTaskBtn")
        page.wait_for_selector("#dialogQqAccounts input")

        all_ids = page.eval_on_selector_all(
            "#taskDialog .account-groups input",
            "els => els.map(el => el.dataset.account)",
        )
        qq_ids = page.eval_on_selector_all(
            "#dialogQqAccounts input", "els => els.map(el => el.dataset.account)"
        )
        bot_ids = page.eval_on_selector_all(
            "#dialogBotAccounts input", "els => els.map(el => el.dataset.account)"
        )
        print(f"accounts total={len(all_ids)} qq={len(qq_ids)} bot={len(bot_ids)}")

        checked = get_checked_ids(page)
        ok = len(checked) == len(all_ids)
        print(f"TEST default all checked: {'PASS' if ok else 'FAIL'} ({len(checked)}/{len(all_ids)})")
        fails += 0 if ok else 1

        page.click("#dialogDeselectQq")
        checked = get_checked_ids(page)
        bot_only = (
            len(checked) == len(bot_ids)
            and all(i in checked for i in bot_ids)
            and all(i not in checked for i in qq_ids)
        )
        print(f"TEST deselect QQ keeps bot: {'PASS' if bot_only else 'FAIL'} {checked}")
        fails += 0 if bot_only else 1

        page.click("#dialogSelectAllCh")
        page.evaluate(
            """() => {
            dialog.videos = [{ id: 'test123', title: '测试视频', link: '', play_addr: '', pic: '', author: '', platform: 'bili' }];
            dialog.selectedVideos = new Set(['test123']);
            renderDialogVideos();
            updateDialogBtns();
        }"""
        )

        captured = None
        page.click("#dialogCreate")
        page.wait_for_timeout(1000)

        if not captured:
            print("TEST submit payload bot-only: FAIL (no POST captured)")
            fails += 1
        else:
            sent = captured.get("account_ids") or []
            ok = (
                len(sent) == len(bot_ids)
                and all(i in sent for i in bot_ids)
                and all(i not in sent for i in qq_ids)
            )
            print(f"TEST submit account_ids bot-only: {'PASS' if ok else 'FAIL'} {sent}")
            fails += 0 if ok else 1

        page.click("#newTaskBtn")
        page.wait_for_selector("#dialogQqAccounts input")
        page.click("#dialogDeselectQq")
        page.click("#dialogDeselectBot")
        checked = get_checked_ids(page)
        print(f"TEST all unchecked count: {len(checked)}")

        page.evaluate(
            """() => {
            dialog.videos = [{ id: 'test456', title: '测试2', link: '', platform: 'bili' }];
            dialog.selectedVideos = new Set(['test456']);
            const ch = state.channels[0];
            if (ch) dialog.selectedChannels = new Set([`${ch.guild_id}:${ch.channel_id}`]);
            updateDialogBtns();
        }"""
        )

        captured = None
        page.on("dialog", lambda d: d.accept())
        page.click("#dialogCreate")
        page.wait_for_timeout(800)

        if captured:
            print(f"TEST empty selection blocked: FAIL (still posted) {captured.get('account_ids')}")
            fails += 1
        else:
            print("TEST empty selection blocked: PASS (no POST)")

        browser.close()

    return fails


if __name__ == "__main__":
    sys.exit(main())
