/**
 * 浏览器测试：新建任务弹窗 account_ids 勾选逻辑
 * 用法: node scripts/test_account_selection.mjs
 */
import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8765";

function getCheckedIds(page) {
  return page.$$eval("#taskDialog .account-groups input:checked", (els) =>
    els.map((el) => el.dataset.account)
  );
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  let capturedBody = null;
  await page.route("**/api/tasks", async (route, request) => {
    if (request.method() === "POST") {
      capturedBody = JSON.parse(request.postData() || "{}");
    }
    await route.continue();
  });

  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click("#newTaskBtn");
  await page.waitForSelector("#dialogQqAccounts input");

  const allIds = await page.$$eval("#taskDialog .account-groups input", (els) =>
    els.map((el) => el.dataset.account)
  );
  const qqIds = await page.$$eval("#dialogQqAccounts input", (els) =>
    els.map((el) => el.dataset.account)
  );
  const botIds = await page.$$eval("#dialogBotAccounts input", (els) =>
    els.map((el) => el.dataset.account)
  );

  console.log("accounts total", allIds.length, "qq", qqIds.length, "bot", botIds.length);

  // 默认全选
  let checked = await getCheckedIds(page);
  console.log("TEST default all checked:", checked.length === allIds.length ? "PASS" : "FAIL", checked.length, allIds.length);

  // 清空 QQ，只留 Bot
  await page.click("#dialogDeselectQq");
  checked = await getCheckedIds(page);
  const expectBotOnly = botIds.every((id) => checked.includes(id)) && qqIds.every((id) => !checked.includes(id));
  console.log("TEST deselect QQ keeps bot:", expectBotOnly ? "PASS" : "FAIL", checked);

  // 模拟最小可提交：选频道 + 注入一条假视频
  await page.evaluate(() => {
    window.__injectVideo = true;
  });
  await page.click("#dialogSelectAllCh");

  // 注入 dialog 状态（绕过搜索）
  await page.evaluate(() => {
    dialog.videos = [{ id: "test123", title: "测试视频", link: "", play_addr: "", pic: "", author: "", platform: "bili" }];
    dialog.selectedVideos = new Set(["test123"]);
    renderDialogVideos();
    updateDialogBtns();
  });

  capturedBody = null;
  await page.click("#dialogCreate");
  await page.waitForTimeout(800);

  if (!capturedBody) {
    console.log("TEST submit payload: FAIL (no POST captured)");
  } else {
    const sent = capturedBody.account_ids || [];
    const botOnlyOk =
      sent.length === botIds.length &&
      botIds.every((id) => sent.includes(id)) &&
      qqIds.every((id) => !sent.includes(id));
    console.log("TEST submit account_ids bot-only:", botOnlyOk ? "PASS" : "FAIL", sent);
  }

  // 全不选应被拦截
  await page.click("#newTaskBtn");
  await page.waitForSelector("#dialogQqAccounts input");
  await page.click("#dialogDeselectQq");
  await page.click("#dialogDeselectBot");
  checked = await getCheckedIds(page);
  console.log("TEST all unchecked count:", checked.length);

  await page.evaluate(() => {
    dialog.videos = [{ id: "test456", title: "测试2", link: "", platform: "bili" }];
    dialog.selectedVideos = new Set(["test456"]);
    dialog.selectedChannels = new Set(["23396421665492266:634579922"]);
    updateDialogBtns();
  });

  capturedBody = null;
  page.once("dialog", (d) => d.accept());
  await page.click("#dialogCreate");
  await page.waitForTimeout(500);

  if (capturedBody) {
    console.log("TEST empty selection blocked: FAIL (still posted)", capturedBody.account_ids);
  } else {
    console.log("TEST empty selection blocked: PASS (no POST)");
  }

  await browser.close();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
