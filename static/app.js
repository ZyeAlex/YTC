const STORAGE_KEY = "channel-poster-v3";
const AUTH_STORAGE_KEY = "channel-poster-auth";

const TASK_TYPE_LABEL = { once: "单次", recurring: "长期", custom: "自定义" };

/** 任务分类标签（平台 + 抖音二级检索方式 + 是否长期） */
function taskKindLabel(t) {
  if (!t) return "-";
  const recurring = t.task_type === "recurring";
  const run = recurring ? "长期" : "单次";
  if (t.platform === "bili") {
    return `B站 · 关键词 · ${run}`;
  }
  if (t.task_type === "custom" || t.source === "manual") {
    return `抖音 · 手动链接 · ${run}`;
  }
  if (t.source === "collection") {
    return `抖音 · 收藏夹 · ${run}`;
  }
  return `抖音 · 关键词 · ${run}`;
}

/** 侧栏任务卡片：从字段构建层级展示，避免与自动生成的 t.name 重复 */
function buildSidebarTaskDisplay(t) {
  const recurring = t.task_type === "recurring";
  const isBili = t.platform === "bili";
  const isManual = t.task_type === "custom" || t.source === "manual";
  const isCollection = t.source === "collection";

  let title = "";
  if (isManual) {
    title = "手动链接";
  } else if (isCollection) {
    title = (t.collection_account_label || "").trim() || "抖音收藏";
  } else {
    title = (t.keyword || "").trim() || "未命名";
  }

  const platform = isBili
    ? { label: "B站", chip: "chip-plat-bili", item: "item-bili" }
    : { label: "抖音", chip: "chip-plat-douyin", item: "item-douyin" };

  let source;
  if (isManual) {
    source = { label: "链接", chip: "chip-src-manual" };
  } else if (isCollection) {
    source = { label: "收藏", chip: "chip-src-collection" };
  } else {
    source = { label: "关键词", chip: "chip-src-keyword" };
  }

  const schedule = recurring
    ? { label: "长期", chip: "chip-sched-recurring" }
    : { label: "单次", chip: "chip-sched-once" };

  return { title, platform, source, schedule, recurring, isBili };
}

function renderSidebarTaskChips(meta) {
  return `
    <div class="sidebar-chip-row" aria-label="任务类型">
      <span class="sidebar-chip ${meta.platform.chip}">${escapeHtml(meta.platform.label)}</span>
      <span class="sidebar-chip-sep" aria-hidden="true"></span>
      <span class="sidebar-chip ${meta.source.chip}">${escapeHtml(meta.source.label)}</span>
      <span class="sidebar-chip-sep" aria-hidden="true"></span>
      <span class="sidebar-chip ${meta.schedule.chip}">${escapeHtml(meta.schedule.label)}</span>
    </div>`;
}

function taskKindTagClass(t) {
  if (!t) return "search";
  if (t.task_type === "recurring") return "recurring";
  if (t.task_type === "custom" || t.source === "manual") return "manual";
  if (t.source === "collection") return "collection";
  if (t.platform === "bili") return "bili";
  return "search";
}

const SEARCH_SORT_LABEL = { default: "综合", recent: "最新" };

const TASK_STATUS = {
  created: "新建",
  running: "运行中",
  paused: "已暂停",
  done: "已完成",
  failed: "失败",
};

const VIDEO_STATUS = {
  pending: "等待",
  waiting: "排队等待",
  downloading: "下载中",
  posting: "发送中",
  done: "已完成",
  skipped: "已跳过",
  failed: "失败",
};

const state = {
  channels: [],
  accounts: [],
  tasks: [],
  selectedTaskId: null,
  taskDetail: null,
  pollTimer: null,
  renderedInfoTaskId: null,
  progressFp: "",
  doneVideosExpanded: false,
  sendingVideos: new Set(),
  mainView: "task",
};

const dialog = {
  mode: "create",
  tab: "bili",
  douyinSubTab: "keyword",
  douyinCookieIndex: 0,
  douyinAccounts: [],
  collectionCursor: 0,
  collectionHasMore: false,
  editTaskId: null,
  lockedVideoIds: new Set(),
  platform: "bili",
  videos: [],
  channelFilter: "all",
  selectedChannels: new Set(),
  selectedVideos: new Set(),
  selectedAccounts: new Set(),
  cron: "",
  searchSort: "recent",
};

let cronPicker = null;
let autoLikeCronPicker = null;

const settings = {
  autoLike: { enabled: false, channels: [] },
  globalLogs: [],
  runningChannels: new Set(),
  selectedChannelKey: null,
  pollTimer: null,
  saveTimer: null,
  saving: false,
};

const auth = {
  accessToken: null,
};

const AUTO_LIKE_DEFAULTS = {
  enabled: false,
  likes_min: 1,
  likes_max: 5,
  schedule_cron: "",
  only_own_posts: true,
  account_ids: [],
  feeds_per_channel: 20,
};


const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function getAccessToken() {
  if (auth.accessToken) return auth.accessToken;
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    auth.accessToken = parsed.accessToken || null;
  } catch {
    return null;
  }
  return auth.accessToken;
}

function saveAuthSession(data) {
  auth.accessToken = data.access_token;
  try {
    localStorage.setItem(
      AUTH_STORAGE_KEY,
      JSON.stringify({ accessToken: auth.accessToken })
    );
  } catch { /* ignore */ }
}

function clearAuthSession() {
  auth.accessToken = null;
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch { /* ignore */ }
}

function showLoginOverlay(message = "") {
  $("#loginOverlay")?.classList.remove("hidden");
  $(".app-shell")?.classList.add("hidden");
  const errEl = $("#loginError");
  if (errEl) {
    if (message) {
      errEl.textContent = message;
      errEl.classList.remove("hidden");
    } else {
      errEl.textContent = "";
      errEl.classList.add("hidden");
    }
  }
}

function hideLoginOverlay() {
  $("#loginOverlay")?.classList.add("hidden");
  $(".app-shell")?.classList.remove("hidden");
}

function formatApiError(detail, fallback = "请求失败") {
  if (detail == null || detail === "") return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const loc = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body").join(".") : "";
        const msg = item.msg || item.message || "";
        return loc ? `${loc}: ${msg}` : msg;
      }
      return String(item);
    }).filter(Boolean);
    return parts.length ? parts.join("；") : fallback;
  }
  if (typeof detail === "object") {
    return detail.message || detail.msg || JSON.stringify(detail);
  }
  return String(detail);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));

  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    clearAuthSession();
    showLoginOverlay("登录已失效，请重新登录");
    throw new Error(formatApiError(data.detail, "未登录"));
  }
  if (!res.ok) throw new Error(formatApiError(data.detail || data.error, res.statusText));
  return data;
}

async function apiPostStream(path, body, onEvent) {
  const headers = { "Content-Type": "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    clearAuthSession();
    showLoginOverlay("登录已失效，请重新登录");
    throw new Error("未登录");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    if (res.status === 404) {
      throw new Error("接口不存在，请重启 ./start.sh 后再试");
    }
    throw new Error(formatApiError(data.detail || data.error, res.statusText || `HTTP ${res.status}`));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;
      const json = line.slice(5).trim();
      if (json) onEvent(JSON.parse(json));
    }
  }
}

async function ensureAuth() {
  const token = getAccessToken();
  if (!token) {
    showLoginOverlay();
    return false;
  }
  try {
    await api("/api/auth/me");
    hideLoginOverlay();
    return true;
  } catch {
    showLoginOverlay();
    return false;
  }
}

async function loginWithToken(loginToken) {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: loginToken.trim() }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(formatApiError(data.detail, "Token 无效"));
  resetAppState();
  saveAuthSession(data);
  hideLoginOverlay();
  return data;
}

async function handleLogin() {
  const token = $("#loginToken")?.value || "";
  const errEl = $("#loginError");
  if (!token.trim()) {
    if (errEl) {
      errEl.textContent = "请输入 Token";
      errEl.classList.remove("hidden");
    }
    return;
  }
  const btn = $("#loginBtn");
  if (btn) btn.disabled = true;
  try {
    await loginWithToken(token);
    await bootstrapApp();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function escapeAttr(s) {
  return String(s).replace(/"/g, "&quot;");
}

function platformLabel(p) {
  return p === "bili" ? "B站" : "抖音";
}

function searchSortLabel(sort) {
  return SEARCH_SORT_LABEL[sort] || SEARCH_SORT_LABEL.recent;
}

function getCronInputEl() {
  return $("#dialogCronPicker");
}

function getCronValue() {
  if (cronPicker) return cronPicker.getValue().trim();
  return (dialog.cron || "").trim();
}

function setCronValue(cron) {
  const value = (cron ?? "").trim();
  dialog.cron = value;
  if (cronPicker) cronPicker.setValue(value);
}

function bindCronInput() {
  const mount = getCronInputEl();
  if (!mount || cronPicker) return;
  cronPicker = new CronPicker(mount, {
    value: dialog.cron || "",
    allowEmpty: true,
    onChange: (v) => {
      dialog.cron = v;
    },
  });
}

function applyScheduleToDialog(t) {
  bindCronInput();
  setCronValue(t?.schedule_cron ?? "");
}

function readScheduleFromDialog() {
  return { schedule_cron: getCronValue() };
}

function getTaskIdFromUrl() {
  return new URLSearchParams(window.location.search).get("task")?.trim() || null;
}

function isAutoLikeRoute() {
  return window.location.pathname === "/auto-like";
}

function isSettingsRoute() {
  return window.location.pathname === "/settings";
}

function updateSidebarNav() {
  const view = state.mainView;
  $("#sidebarNavTasks")?.classList.toggle("active", view === "task");
  $("#sidebarNavAutoLike")?.classList.toggle("active", view === "auto-like");
  $("#sidebarNavSettings")?.classList.toggle("active", view === "settings");
}

function navigateToTasks() {
  if (state.mainView === "task") return;
  const id = state.selectedTaskId;
  if (id && state.tasks.some((t) => t.task_id === id)) {
    selectTask(id);
    return;
  }
  state.selectedTaskId = null;
  state.taskDetail = null;
  clearTaskUrl();
  showEmptyState();
}

function setAutoLikeUrl(replace = false) {
  const url = new URL(`${window.location.origin}/auto-like`);
  const stateObj = { view: "auto-like" };
  if (replace) {
    window.history.replaceState(stateObj, "", url);
  } else {
    window.history.pushState(stateObj, "", url);
  }
}

function setSettingsUrl(replace = false) {
  const url = new URL(`${window.location.origin}/settings`);
  const stateObj = { view: "settings" };
  if (replace) {
    window.history.replaceState(stateObj, "", url);
  } else {
    window.history.pushState(stateObj, "", url);
  }
}

function setTaskUrl(taskId, { replace = false } = {}) {
  const url = new URL(`${window.location.origin}/`);
  url.searchParams.set("task", taskId);
  const stateObj = { view: "task", taskId };
  if (replace) {
    window.history.replaceState(stateObj, "", url);
  } else {
    window.history.pushState(stateObj, "", url);
  }
}

function clearTaskUrl() {
  const url = new URL(`${window.location.origin}/`);
  window.history.replaceState({ view: "task" }, "", url);
}

function getTaskShareUrl(taskId) {
  const url = new URL(`${window.location.origin}/`);
  url.searchParams.set("task", taskId);
  return url.toString();
}

function saveSelectedTask() {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ selectedTaskId: state.selectedTaskId })
    );
  } catch { /* ignore */ }
}

function loadSelectedTask() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw).selectedTaskId : null;
  } catch {
    return null;
  }
}

function resetAppState() {
  state.channels = [];
  state.accounts = [];
  state.tasks = [];
  state.selectedTaskId = null;
  state.taskDetail = null;
  state.renderedInfoTaskId = null;
  state.progressFp = "";
  state.sendingVideos = new Set();
  settings.autoLike = { enabled: false, channels: [] };
  settings.globalLogs = [];
  settings.runningChannels = new Set();
  settings.selectedChannelKey = null;
}

// ── Meta ──

async function loadMeta() {
  const [chRes, accRes] = await Promise.all([
    api("/api/channels"),
    api("/api/accounts"),
  ]);
  state.channels = chRes.channels;
  state.accounts = accRes.accounts;
}

// ── Settings ──

function showSettingsView() {
  hideAllMainPanels();
  state.mainView = "settings";
  stopAutoLikePolling();
  $("#settingsView")?.classList.remove("hidden");
  renderSidebar();
  updateSidebarNav();
}

const settingsBaseline = {
  biliCookies: [],
  douyinCookies: [],
  accountsJson: "",
  channelsJson: "",
};

function createCookieRow(value = "") {
  const row = document.createElement("div");
  row.className = "cookie-row";
  const input = document.createElement("textarea");
  input.className = "cookie-input";
  input.rows = 3;
  input.spellcheck = false;
  input.placeholder = "粘贴 Cookie 字符串";
  input.value = value;
  const del = document.createElement("button");
  del.type = "button";
  del.className = "cookie-row-del";
  del.title = "删除";
  del.textContent = "✕";
  row.append(input, del);
  return row;
}

function renderCookieList(container, cookies) {
  if (!container) return;
  container.replaceChildren();
  const list = Array.isArray(cookies) ? cookies.filter(Boolean) : [];
  list.forEach((cookie) => container.appendChild(createCookieRow(cookie)));
}

function collectCookiesFromList(container) {
  if (!container) return [];
  return [...container.querySelectorAll(".cookie-input")]
    .map((el) => el.value.trim())
    .filter(Boolean);
}

function getBiliCookiesFromForm() {
  return collectCookiesFromList($("#settingsBiliCookiesList"));
}

function getDouyinCookiesFromForm() {
  return collectCookiesFromList($("#settingsDouyinCookiesList"));
}

function setSettingsBaselineFromProfile(profile) {
  settingsBaseline.biliCookies = [...(profile.bili_cookies || [])];
  settingsBaseline.douyinCookies = [...(profile.douyin_cookies || [])];
  settingsBaseline.accountsJson = JSON.stringify(
    profile.accounts || { qq_accounts: [], bot_accounts: [] },
    null,
    2
  );
  settingsBaseline.channelsJson = JSON.stringify(profile.channels || [], null, 2);
  updateSettingsSaveButtons();
}

function updateSettingsSaveButtons() {
  const biliDirty = JSON.stringify(getBiliCookiesFromForm()) !== JSON.stringify(settingsBaseline.biliCookies);
  const douyinDirty = JSON.stringify(getDouyinCookiesFromForm()) !== JSON.stringify(settingsBaseline.douyinCookies);
  const accountsEl = $("#settingsAccountsJson");
  const channelsEl = $("#settingsChannelsJson");
  const accountsDirty = (accountsEl?.value || "") !== settingsBaseline.accountsJson;
  const channelsDirty = (channelsEl?.value || "") !== settingsBaseline.channelsJson;

  $("#saveBiliCookiesBtn")?.classList.toggle("hidden", !biliDirty);
  $("#saveDouyinCookiesBtn")?.classList.toggle("hidden", !douyinDirty);
  $("#saveAccountsBtn")?.classList.toggle("hidden", !accountsDirty);
  $("#saveChannelsBtn")?.classList.toggle("hidden", !channelsDirty);
}

function addCookieRow(kind) {
  const list = kind === "bili" ? $("#settingsBiliCookiesList") : $("#settingsDouyinCookiesList");
  list?.appendChild(createCookieRow());
  updateSettingsSaveButtons();
}

async function loadSettingsProfile() {
  const profile = await api("/api/settings/profile");
  const accEl = $("#settingsAccountsJson");
  const chEl = $("#settingsChannelsJson");
  renderCookieList($("#settingsBiliCookiesList"), profile.bili_cookies);
  renderCookieList($("#settingsDouyinCookiesList"), profile.douyin_cookies);
  if (accEl) {
    accEl.value = JSON.stringify(
      profile.accounts || { qq_accounts: [], bot_accounts: [] },
      null,
      2
    );
  }
  if (chEl) {
    chEl.value = JSON.stringify(profile.channels || [], null, 2);
  }
  setSettingsBaselineFromProfile(profile);
}

async function openSettingsView({ updateUrl = true } = {}) {
  try {
    await loadSettingsProfile();
    await loadSettingsFilterPatterns();
    showSettingsView();
    if (updateUrl && !isSettingsRoute()) {
      setSettingsUrl();
    }
  } catch (e) {
    console.error("加载设置失败:", e);
    showSettingsView();
    if (updateUrl && !isSettingsRoute()) {
      setSettingsUrl();
    }
    throw e;
  }
}

function goBackFromSettings() {
  navigateToTasks();
}

async function logoutUser() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch { /* ignore */ }
  clearAuthSession();
  resetAppState();
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  stopAutoLikePolling();
  const loginInput = $("#loginToken");
  if (loginInput) loginInput.value = "";
  window.history.replaceState({}, "", "/");
  showLoginOverlay();
}

async function saveBiliCookiesSettings() {
  const btn = $("#saveBiliCookiesBtn");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/settings/cookies/bili", {
      method: "PUT",
      body: JSON.stringify({ cookies: getBiliCookiesFromForm() }),
    });
    renderCookieList($("#settingsBiliCookiesList"), data.bili_cookies);
    settingsBaseline.biliCookies = [...(data.bili_cookies || [])];
    updateSettingsSaveButtons();
    alert("B站 Cookie 已保存");
  } catch (e) {
    alert(`保存失败: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveDouyinCookiesSettings() {
  const btn = $("#saveDouyinCookiesBtn");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/settings/cookies/douyin", {
      method: "PUT",
      body: JSON.stringify({ cookies: getDouyinCookiesFromForm() }),
    });
    renderCookieList($("#settingsDouyinCookiesList"), data.douyin_cookies);
    settingsBaseline.douyinCookies = [...(data.douyin_cookies || [])];
    updateSettingsSaveButtons();
    alert("抖音 Cookie 已保存");
  } catch (e) {
    alert(`保存失败: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveAccountsSettings() {
  const raw = $("#settingsAccountsJson")?.value?.trim();
  if (!raw) return;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    alert("账号 JSON 格式错误");
    return;
  }
  const btn = $("#saveAccountsBtn");
  if (btn) btn.disabled = true;
  try {
    await api("/api/settings/accounts", {
      method: "PUT",
      body: JSON.stringify({
        qq_accounts: parsed.qq_accounts || [],
        bot_accounts: parsed.bot_accounts || [],
      }),
    });
    await loadMeta();
    settingsBaseline.accountsJson = $("#settingsAccountsJson")?.value || "";
    updateSettingsSaveButtons();
    alert("发帖账号已保存");
  } catch (e) {
    alert(`保存失败: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function updateAddAccountRowNamePlaceholder(row) {
  const type = row.querySelector(".add-account-row-type")?.value || "bot";
  const nameInput = row.querySelector(".add-account-row-name");
  if (nameInput) nameInput.placeholder = type === "qq" ? "必填" : "可留空";
}

function createAddAccountRow({ name = "", token = "", accountType = "bot" } = {}) {
  const row = document.createElement("div");
  row.className = "add-account-row";
  const qqSel = accountType === "qq" ? "selected" : "";
  const botSel = accountType === "bot" ? "selected" : "";
  row.innerHTML = `
    <select class="add-account-row-type">
      <option value="qq" ${qqSel}>QQ</option>
      <option value="bot" ${botSel}>Bot</option>
    </select>
    <input type="text" class="add-account-row-name" placeholder="可留空" value="${escapeAttr(name)}" autocomplete="off" />
    <input type="text" class="add-account-row-token" placeholder="bot:v1_..." value="${escapeAttr(token)}" autocomplete="off" spellcheck="false" />
    <button type="button" class="add-account-row-del" title="删除">✕</button>
  `;
  updateAddAccountRowNamePlaceholder(row);
  row.querySelector(".add-account-row-type")?.addEventListener("change", () => {
    updateAddAccountRowNamePlaceholder(row);
  });
  return row;
}

function renderAddAccountRows(rows = [{ name: "", token: "", accountType: "bot" }]) {
  const container = $("#addAccountRows");
  if (!container) return;
  container.innerHTML = "";
  rows.forEach((row) => {
    container.appendChild(createAddAccountRow(row));
  });
  updateAddAccountRowDeleteButtons();
}

function updateAddAccountRowDeleteButtons() {
  const rows = $$(".add-account-row");
  rows.forEach((row) => {
    const del = row.querySelector(".add-account-row-del");
    if (del) del.classList.toggle("hidden", rows.length <= 1);
  });
}

function addAddAccountRow() {
  const container = $("#addAccountRows");
  if (!container) return;
  const row = createAddAccountRow();
  container.appendChild(row);
  updateAddAccountRowDeleteButtons();
  row.querySelector(".add-account-row-type")?.focus();
}

function collectAddAccountEntries() {
  const rows = $$(".add-account-row");
  const entries = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const accountType = row.querySelector(".add-account-row-type")?.value || "bot";
    const name = row.querySelector(".add-account-row-name")?.value?.trim() || "";
    const token = row.querySelector(".add-account-row-token")?.value?.trim() || "";
    if (!name && !token) continue;
    if (!token) {
      throw new Error(`第 ${i + 1} 行：请填写 Token`);
    }
    if (accountType === "qq" && !name) {
      throw new Error(`第 ${i + 1} 行：QQ 账号必须填写名称`);
    }
    entries.push({ name, token, account_type: accountType });
  }
  if (!entries.length) {
    throw new Error("请至少填写一个账号");
  }
  return entries;
}

function resetAddAccountProgress() {
  $("#addAccountProgress")?.classList.add("hidden");
  const fill = $("#addAccountProgressFill");
  if (fill) fill.style.width = "0%";
  const log = $("#addAccountProgressLog");
  if (log) log.innerHTML = "";
  const label = $("#addAccountProgressLabel");
  if (label) label.textContent = "准备中…";
  const count = $("#addAccountProgressCount");
  if (count) count.textContent = "0/0";
}

function handleAddAccountProgressEvent(ev) {
  const wrap = $("#addAccountProgress");
  const label = $("#addAccountProgressLabel");
  const count = $("#addAccountProgressCount");
  const fill = $("#addAccountProgressFill");
  const log = $("#addAccountProgressLog");
  const statusEl = $("#addAccountStatus");

  if (ev.type === "error") {
    const errMsg = formatApiError(ev.message, "添加失败");
    statusEl?.classList.remove("hidden");
    statusEl?.classList.add("error");
    if (statusEl) statusEl.textContent = errMsg;
    if (label) label.textContent = "失败";
    return { error: errMsg };
  }

  wrap?.classList.remove("hidden");
  statusEl?.classList.add("hidden");

  const accPrefix =
    ev.accounts_total > 1 && ev.account_index
      ? `[${ev.account_index}/${ev.accounts_total}] `
      : "";

  if (ev.type === "batch_start") {
    if (label) {
      label.textContent = `共 ${ev.accounts_total} 个账号，每个加入 ${ev.guilds_total} 个频道`;
    }
    if (count) count.textContent = `0/${ev.guilds_total || 0}`;
    if (fill) fill.style.width = "0%";
    if (log) log.innerHTML = "";
    return {};
  }
  if (ev.type === "account_start") {
    if (log) {
      log.innerHTML += `<div class="add-account-log-line account-head">—— ${escapeHtml(ev.name)} (${escapeHtml(ev.account_id || "")}) ——</div>`;
      log.scrollTop = log.scrollHeight;
    }
    return {};
  }
  if (ev.type === "account_skipped") {
    if (log) {
      log.innerHTML += `<div class="add-account-log-line fail">✗ 跳过 ${escapeHtml(ev.name)}：${escapeHtml(ev.error || "")}</div>`;
      log.scrollTop = log.scrollHeight;
    }
    return {};
  }
  if (ev.type === "start") {
    if (label) {
      label.textContent = `${accPrefix}开始加入 ${ev.total} 个频道…`;
    }
    if (count) count.textContent = `0/${ev.total}`;
    return {};
  }
  if (ev.type === "joining") {
    if (label) label.textContent = `${accPrefix}正在加入：${ev.channel}`;
    if (count) count.textContent = `${Math.max(0, ev.index - 1)}/${ev.total}`;
    return {};
  }
  if (ev.type === "joined") {
    const pct = ev.total ? Math.round((ev.index / ev.total) * 100) : 0;
    if (fill) fill.style.width = `${pct}%`;
    if (count) count.textContent = `${ev.index}/${ev.total}`;
    if (log) {
      const cls = ev.ok ? "ok" : "fail";
      const icon = ev.ok ? "✓" : "✗";
      const err = ev.error ? ` — ${ev.error}` : "";
      log.innerHTML += `<div class="add-account-log-line ${cls}">${accPrefix}${icon} ${escapeHtml(ev.channel)}${escapeHtml(err)}</div>`;
      log.scrollTop = log.scrollHeight;
    }
    return {};
  }
  if (ev.type === "wait") {
    if (label) label.textContent = `${accPrefix}等待 ${ev.seconds}s 后加入：${ev.next}`;
    return {};
  }
  if (ev.type === "account_done") {
    if (log) {
      log.innerHTML += `<div class="add-account-log-line ok">${accPrefix}✓ 已保存 ${escapeHtml(ev.account?.name || "")}</div>`;
      log.scrollTop = log.scrollHeight;
    }
    return { accountSaved: ev };
  }
  if (ev.type === "batch_done") {
    if (label) label.textContent = "全部完成";
    if (fill) fill.style.width = "100%";
    if (count) count.textContent = `${ev.added}/${ev.accounts_total}`;
    return { done: ev };
  }
  if (ev.type === "done") {
    if (label) label.textContent = "完成";
    if (fill) fill.style.width = "100%";
    if (count) count.textContent = `${ev.joined}/${ev.total}`;
    return { done: ev };
  }
  return {};
}

function openAddAccountDialog() {
  const statusEl = $("#addAccountStatus");
  if (statusEl) {
    statusEl.classList.add("hidden");
    statusEl.classList.remove("error", "success");
    statusEl.textContent = "";
  }
  resetAddAccountProgress();
  renderAddAccountRows();
  setAddAccountFormDisabled(false);
  $("#addAccountDialog")?.classList.remove("hidden");
}

function setAddAccountFormDisabled(disabled) {
  $("#addAccountRowBtn").disabled = disabled;
  $$(".add-account-row-type, .add-account-row-name, .add-account-row-token").forEach((el) => {
    el.disabled = disabled;
  });
  $$(".add-account-row-del").forEach((el) => {
    el.disabled = disabled;
  });
  const btn = $("#submitAddAccountsBtn");
  if (btn) {
    btn.disabled = disabled;
    btn.textContent = disabled ? "添加中…" : "开始添加";
  }
  const closeBtn = $("#closeAddAccountDialog");
  if (closeBtn) closeBtn.disabled = disabled;
}

let addAccountRunning = false;

function closeAddAccountDialog() {
  if (addAccountRunning) return;
  $("#addAccountDialog")?.classList.add("hidden");
}

async function submitAddAccounts() {
  let entries;
  try {
    entries = collectAddAccountEntries();
  } catch (e) {
    alert(e.message);
    return;
  }

  resetAddAccountProgress();
  addAccountRunning = true;
  setAddAccountFormDisabled(true);

  let doneData = null;
  let streamError = null;

  try {
    await apiPostStream(
      "/api/accounts/add-stream",
      { accounts: entries },
      (ev) => {
        const result = handleAddAccountProgressEvent(ev);
        if (result.error) streamError = result.error;
        if (result.done) doneData = result.done;
      }
    );
  } catch (e) {
    const statusEl = $("#addAccountStatus");
    if (statusEl) {
      statusEl.classList.remove("hidden");
      statusEl.classList.add("error");
      statusEl.textContent = e.message || "添加失败";
    } else {
      alert(`添加失败: ${e.message}`);
    }
    setAddAccountFormDisabled(false);
    addAccountRunning = false;
    return;
  }

  if (streamError) {
    setAddAccountFormDisabled(false);
    addAccountRunning = false;
    return;
  }

  if (!doneData) {
    const statusEl = $("#addAccountStatus");
    if (statusEl) {
      statusEl.classList.remove("hidden");
      statusEl.classList.add("error");
      statusEl.textContent = "未收到完成结果";
    }
    setAddAccountFormDisabled(false);
    addAccountRunning = false;
    return;
  }

  const accEl = $("#settingsAccountsJson");
  if (accEl && doneData.accounts) {
    accEl.value = JSON.stringify(doneData.accounts, null, 2);
  }
  await loadMeta();
  addAccountRunning = false;

  const statusEl = $("#addAccountStatus");
  if (statusEl) {
    statusEl.classList.remove("hidden", "error");
    statusEl.classList.add("success");
    const skipped = doneData.skipped || 0;
    const skippedMsg = skipped ? `，跳过 ${skipped} 个` : "";
    statusEl.textContent = `成功添加 ${doneData.added}/${doneData.accounts_total} 个账号${skippedMsg}`;
  }

  setTimeout(() => closeAddAccountDialog(), 1500);
}

function openAddChannelDialog() {
  const input = $("#addChannelText");
  const statusEl = $("#addChannelStatus");
  if (input) input.value = "";
  if (statusEl) {
    statusEl.classList.add("hidden");
    statusEl.classList.remove("error", "success");
    statusEl.textContent = "";
  }
  $("#addChannelCategory").value = "游戏";
  $("#addChannelDialog")?.classList.remove("hidden");
}

function closeAddChannelDialog() {
  $("#addChannelDialog")?.classList.add("hidden");
}

async function addChannelFromShare() {
  const text = $("#addChannelText")?.value?.trim();
  if (!text) {
    alert("请粘贴分享文案或链接");
    return;
  }
  const category = $("#addChannelCategory")?.value || "游戏";
  const btn = $("#addChannelBtn");
  const cancelBtn = $("#addChannelCancelBtn");
  const statusEl = $("#addChannelStatus");
  if (btn) btn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;
  if (statusEl) {
    statusEl.classList.remove("hidden", "error", "success");
    statusEl.textContent = "正在解析链接、加入频道并保存…";
  }
  try {
    const data = await api("/api/channels/add-from-share", {
      method: "POST",
      body: JSON.stringify({ text, category }),
    });
    const ch = data.channel || {};
    const joined = data.joined ?? 0;
    const total = data.total ?? 0;
    const failed = (data.join_results || []).filter((r) => !r.ok);
    const chEl = $("#settingsChannelsJson");
    if (chEl && Array.isArray(data.channels)) {
      chEl.value = JSON.stringify(data.channels, null, 2);
      settingsBaseline.channelsJson = chEl.value;
      updateSettingsSaveButtons();
    }
    await loadMeta();
    closeAddChannelDialog();
    const failedMsg = failed.length
      ? `\n部分账号失败：${failed.map((r) => `${r.account}: ${r.error || "未知"}`).join("；")}`
      : "";
    alert(
      `已添加「${ch.name || ""}」\n` +
        `guild_id=${ch.guild_id || ""}\n` +
        `channel_id=${ch.channel_id || ""}\n` +
        `账号加入 ${joined}/${total}${failedMsg}`
    );
  } catch (e) {
    if (statusEl) {
      statusEl.classList.add("error");
      statusEl.textContent = e.message || "添加失败";
    } else {
      alert(`添加失败: ${e.message}`);
    }
  } finally {
    if (btn) btn.disabled = false;
    if (cancelBtn) cancelBtn.disabled = false;
  }
}

async function saveChannelsSettings() {
  const raw = $("#settingsChannelsJson")?.value?.trim();
  if (!raw) return;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    alert("频道 JSON 格式错误");
    return;
  }
  if (!Array.isArray(parsed)) {
    alert("频道必须是 JSON 数组");
    return;
  }
  const btn = $("#saveChannelsBtn");
  if (btn) btn.disabled = true;
  try {
    await api("/api/settings/channels", {
      method: "PUT",
      body: JSON.stringify({ channels: parsed }),
    });
    await loadMeta();
    settingsBaseline.channelsJson = $("#settingsChannelsJson")?.value || "";
    updateSettingsSaveButtons();
    alert("频道列表已保存");
  } catch (e) {
    alert(`保存失败: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadSettingsFilterPatterns() {
  const data = await api("/api/filter-patterns");
  const el = $("#settingsFilterPatterns");
  if (el) el.value = (data.patterns || []).join("\n");
}

async function saveSettingsFilterPatterns() {
  const patterns = parseFilterPatternsText($("#settingsFilterPatterns")?.value || "");
  const btn = $("#saveSettingsFilterBtn");
  if (btn) btn.disabled = true;
  try {
    await api("/api/filter-patterns", {
      method: "PUT",
      body: JSON.stringify({ patterns }),
    });
    alert("过滤词已保存");
  } catch (e) {
    alert(`保存失败: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function bindSettingsEvents() {
  $("#sidebarNavSettings")?.addEventListener("click", () => {
    if (state.mainView === "settings") return;
    openSettingsView().catch((e) => alert(`加载设置失败: ${e.message}`));
  });

  $("#saveAccountsBtn")?.addEventListener("click", saveAccountsSettings);
  $("#saveBiliCookiesBtn")?.addEventListener("click", saveBiliCookiesSettings);
  $("#saveDouyinCookiesBtn")?.addEventListener("click", saveDouyinCookiesSettings);
  $("#saveChannelsBtn")?.addEventListener("click", saveChannelsSettings);
  $("#addBiliCookieBtn")?.addEventListener("click", () => addCookieRow("bili"));
  $("#addDouyinCookieBtn")?.addEventListener("click", () => addCookieRow("douyin"));
  $("#settingsBiliCookiesList")?.addEventListener("input", updateSettingsSaveButtons);
  $("#settingsDouyinCookiesList")?.addEventListener("input", updateSettingsSaveButtons);
  $("#settingsBiliCookiesList")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".cookie-row-del");
    if (!btn) return;
    btn.closest(".cookie-row")?.remove();
    updateSettingsSaveButtons();
  });
  $("#settingsDouyinCookiesList")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".cookie-row-del");
    if (!btn) return;
    btn.closest(".cookie-row")?.remove();
    updateSettingsSaveButtons();
  });
  $("#settingsAccountsJson")?.addEventListener("input", updateSettingsSaveButtons);
  $("#settingsChannelsJson")?.addEventListener("input", updateSettingsSaveButtons);
  $("#openAddAccountDialogBtn")?.addEventListener("click", openAddAccountDialog);
  $("#submitAddAccountsBtn")?.addEventListener("click", submitAddAccounts);
  $("#addAccountRowBtn")?.addEventListener("click", addAddAccountRow);
  $("#closeAddAccountDialog")?.addEventListener("click", closeAddAccountDialog);
  $("#addAccountRows")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".add-account-row-del");
    if (!btn || addAccountRunning) return;
    const row = btn.closest(".add-account-row");
    if (row) {
      row.remove();
      if (!$$(".add-account-row").length) addAddAccountRow();
      else updateAddAccountRowDeleteButtons();
    }
  });
  $("#addAccountDialog")?.addEventListener("click", (e) => {
    if (e.target === $("#addAccountDialog")) closeAddAccountDialog();
  });
  $("#openAddChannelDialogBtn")?.addEventListener("click", openAddChannelDialog);
  $("#addChannelBtn")?.addEventListener("click", addChannelFromShare);
  $("#addChannelCancelBtn")?.addEventListener("click", closeAddChannelDialog);
  $("#closeAddChannelDialog")?.addEventListener("click", closeAddChannelDialog);
  $("#addChannelDialog")?.addEventListener("click", (e) => {
    if (e.target === $("#addChannelDialog")) closeAddChannelDialog();
  });
  $("#logoutBtn")?.addEventListener("click", logoutUser);
  $("#saveSettingsFilterBtn")?.addEventListener("click", saveSettingsFilterPatterns);

  $("#loginBtn")?.addEventListener("click", handleLogin);
  $("#loginToken")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleLogin();
  });
}

function channelKey(ch) {
  return `${ch.guild_id}:${ch.channel_id}`;
}

function formatSentTime(ts) {
  if (!ts) return "";
  const parts = ts.split(" ");
  return parts.length > 1 ? parts[1] : ts;
}

function progressFingerprint(videos) {
  return JSON.stringify(
    videos.map((v) => ({
      id: v.id,
      status: v.status,
      account: v.account,
      message: v.message,
      started_at: v.started_at,
      sent_at: v.sent_at,
      channels: (v.channels || []).map((c) => ({ status: c.status, sent_at: c.sent_at })),
    }))
  );
}

function videoTimeHtml(v) {
  if (v.sent_at) {
    return `<div class="progress-time">发送时间: ${escapeHtml(v.sent_at)}</div>`;
  }
  if (v.started_at) {
    return `<div class="progress-time">开始时间: ${escapeHtml(v.started_at)}</div>`;
  }
  return '<div class="progress-time"></div>';
}

function channelTagsHtml(channels) {
  return (channels || [])
    .map((c) => {
      const cls = `ch-tag status-${c.status}`;
      const icon = c.status === "done" ? "✓" : c.status === "failed" ? "✗" : c.status === "posting" ? "…" : "○";
      const time = c.sent_at ? ` ${formatSentTime(c.sent_at)}` : "";
      const title = c.sent_at ? `${c.name} · ${c.sent_at}` : c.name;
      return `<span class="${cls}" title="${escapeHtml(title)}">${icon} ${escapeHtml(c.name)}${time ? `<span class="ch-time">${escapeHtml(time.trim())}</span>` : ""}</span>`;
    })
    .join("");
}

function getActiveTaskId() {
  return state.selectedTaskId || state.taskDetail?.task_id || null;
}

function clearTaskView() {
  state.selectedTaskId = null;
  state.taskDetail = null;
  state.renderedInfoTaskId = null;
  state.progressFp = "";
  saveSelectedTask();
  clearTaskUrl();
  showEmptyState();
}

// ── Sidebar task list ──

function renderSidebar() {
  const list = $("#sidebarTaskList");
  if (!state.tasks.length) {
    list.innerHTML = '<div class="empty-hint">暂无任务</div>';
    return;
  }

  list.innerHTML = state.tasks
    .map((t) => {
      const sel =
        state.mainView === "task" && t.task_id === state.selectedTaskId ? " active" : "";
      const statusLabel = TASK_STATUS[t.status] || t.status;
      const progress =
        t.videos_done != null ? `${t.videos_done}/${t.video_count}` : `${t.video_count} 视频`;
      const meta = buildSidebarTaskDisplay(t);
      const running = t.status === "running" ? " is-running" : "";
      const recurring = meta.recurring ? " is-recurring" : "";
      return `
        <div class="sidebar-item ${meta.platform.item}${sel}${running}${recurring}" data-id="${t.task_id}">
          <div class="sidebar-item-accent" aria-hidden="true"></div>
          <div class="sidebar-item-inner">
            <div class="sidebar-item-head">
              ${renderSidebarTaskChips(meta)}
              <span class="task-badge status-${t.status}">${statusLabel}</span>
            </div>
            <div class="sidebar-item-title" title="${escapeAttr(t.name)}">${escapeHtml(meta.title)}</div>
            <div class="sidebar-item-foot">
              <span class="sidebar-item-progress">${progress}</span>
            </div>
          </div>
        </div>`;
    })
    .join("");
}

async function refreshTasks() {
  try {
    const data = await api("/api/tasks");
    state.tasks = data.tasks || [];
  } catch (e) {
    console.error("refreshTasks failed:", e);
    state.tasks = [];
  }
  renderSidebar();
  syncSelectedTask();
}

function syncSelectedTask() {
  if (state.mainView === "auto-like" || state.mainView === "settings") return;
  if (!state.selectedTaskId) {
    if (state.taskDetail) clearTaskView();
    return;
  }
  if (state.tasks.some((t) => t.task_id === state.selectedTaskId)) return;
  clearTaskView();
}

async function pollDetail() {
  await refreshTasks();
  if (state.mainView === "auto-like" || state.mainView === "settings") return;
  if (!state.selectedTaskId) return;

  if (!state.tasks.some((t) => t.task_id === state.selectedTaskId)) {
    syncSelectedTask();
    return;
  }

  try {
    const detail = await api(`/api/tasks/${state.selectedTaskId}`);
    state.taskDetail = detail;
    renderTaskDetail();
  } catch {
    syncSelectedTask();
  }
}

async function selectTask(taskId, { updateUrl = true, replaceUrl = false } = {}) {
  state.selectedTaskId = taskId;
  state.renderedInfoTaskId = null;
  state.progressFp = "";
  state.doneVideosExpanded = false;
  saveSelectedTask();
  if (updateUrl) setTaskUrl(taskId, { replace: replaceUrl });
  renderSidebar();
  updateSidebarNav();

  try {
    state.taskDetail = await api(`/api/tasks/${taskId}`);
    renderTaskDetail(true);
  } catch (e) {
    state.selectedTaskId = null;
    state.taskDetail = null;
    clearTaskUrl();
    showEmptyState();
    console.error(e);
  }
}

function hideAllMainPanels() {
  $("#emptyState")?.classList.add("hidden");
  $("#taskDetail")?.classList.add("hidden");
  $("#autoLikeView")?.classList.add("hidden");
  $("#settingsView")?.classList.add("hidden");
}

function showEmptyState() {
  hideAllMainPanels();
  state.mainView = "task";
  stopAutoLikePolling();
  $("#emptyState").classList.remove("hidden");
  const delBtn = $("#deleteTaskBtn");
  if (delBtn) delBtn.disabled = true;
  renderSidebar();
  updateSidebarNav();
}

function renderTaskDetail(force = false) {
  const t = state.taskDetail;
  if (!t) { showEmptyState(); return; }

  hideAllMainPanels();
  state.mainView = "task";
  stopAutoLikePolling();
  $("#taskDetail").classList.remove("hidden");
  renderSidebar();
  updateSidebarNav();

  $("#detailName").textContent = t.name;
  const kindBadge = $("#detailKindBadge");
  if (kindBadge) {
    kindBadge.textContent = taskKindLabel(t);
    kindBadge.className = `task-kind-badge ${taskKindTagClass(t)}`;
  }
  const badge = $("#detailStatus");
  badge.textContent = TASK_STATUS[t.status] || t.status;
  badge.className = `task-badge status-${t.status}`;

  const startBtn = $("#startTaskBtn");
  const pauseBtn = $("#pauseTaskBtn");
  const editBtn = $("#editTaskBtn");
  const delBtn = $("#deleteTaskBtn");
  const canStart = t.status === "created" || t.status === "paused";
  startBtn.classList.toggle("hidden", !canStart);
  pauseBtn.classList.toggle("hidden", t.status !== "running");
  editBtn.classList.toggle("hidden", t.status === "running");
  startBtn.textContent = (t.videos_done || 0) > 0 ? "继续执行" : "启动任务";
  delBtn.disabled = false;

  const appendBtn = $("#appendLinksBtn");
  appendBtn?.classList.toggle("hidden", t.task_type !== "custom");
  appendBtn?.toggleAttribute("disabled", t.status === "running" && false); // 运行中也可追加

  const syncCollectionBtn = $("#syncCollectionBtn");
  syncCollectionBtn?.classList.toggle("hidden", t.source !== "collection");

  if (force || state.renderedInfoTaskId !== t.task_id) {
    state.renderedInfoTaskId = t.task_id;
    const chNames = (t.channels || []).map((c) => c.name).join("、") || "-";
    const accNames = (t.account_names || []).join("、") || "-";
    const isCustom = t.task_type === "custom" || t.source === "manual";
    const isRecurring = t.task_type === "recurring";
    const isCollection = t.source === "collection";
    const batchLabel = isCollection ? "已同步批次" : "已搜批次";
    $("#infoGrid").innerHTML = `
      ${isCollection ? `<div class="info-card"><label>收藏来源</label><span>${escapeHtml(t.collection_account_label || "抖音收藏")}</span></div>` : ""}
      ${isCustom || isCollection ? "" : `<div class="info-card"><label>关键词</label><span>${escapeHtml(t.keyword || "-")}</span></div>`}
      ${isCustom || isCollection ? "" : `<div class="info-card"><label>搜索排序</label><span>${searchSortLabel(t.search_sort)}</span></div>`}
      <div class="info-card"><label>视频数</label><span>${t.video_count}</span></div>
      ${isRecurring ? `<div class="info-card"><label>${batchLabel}</label><span>${t.batch_count || 0}</span></div>` : ""}
      <div class="info-card"><label>频道数</label><span>${t.channel_count}</span></div>
      <div class="info-card"><label>发送计划</label><span>${escapeHtml(t.schedule_desc || t.schedule_cron || "-")}</span></div>
      <div class="info-card"><label>Cron</label><span><code>${escapeHtml(t.schedule_cron || "-")}</code></span></div>
      <div class="info-card"><label>创建时间</label><span>${t.created_at || "-"}</span></div>
      <div class="info-card wide"><label>目标频道</label><span>${escapeHtml(chNames)}</span></div>
      <div class="info-card wide"><label>发送账号</label><span>${escapeHtml(accNames)}</span></div>
    `;
    $("#copyTaskLinkBtn")?.addEventListener("click", copyTaskLink);
  }

  updateVideoProgress(t.video_progress || [], force, t.status, t);
  renderTaskLogs(t.logs || []);
}

function renderTaskLogs(logs) {
  const el = $("#taskRunLogs");
  if (!el) return;
  if (!logs.length) {
    el.innerHTML = '<div class="log-line">暂无日志</div>';
    return;
  }
  el.innerHTML = [...logs].reverse().map((entry) => {
    const level = entry.level === "warn" ? " log-warn" : entry.level === "error" ? " log-error" : entry.level === "success" ? " log-success" : "";
    const time = entry.time ? `<span class="log-time">${escapeHtml(entry.time)}</span> ` : "";
    return `<div class="log-line${level}">${time}${escapeHtml(entry.message || "")}</div>`;
  }).join("");
}

function isProgressVideoDone(vp) {
  return vp.status === "done" || vp.status === "skipped";
}

/** 与后端 sends_newest_last 一致：收藏 / 搜索「最新」从列表尾部往前发 */
function shouldSendNewestLast(task) {
  if (!task) return false;
  if (task.source === "collection") return true;
  if (task.source === "manual" || task.task_type === "custom") return false;
  return (task.search_sort || "recent") === "recent";
}

/** 任务详情：按发送顺序展示（下一个要发的在最上） */
function orderProgressForDisplay(videos, task) {
  const n = videos.length;
  if (!n) return [];
  if (shouldSendNewestLast(task)) {
    return [...videos].reverse();
  }
  return [...videos];
}

function splitProgressForDisplay(videos, task) {
  const ordered = orderProgressForDisplay(videos, task);
  const active = [];
  const done = [];
  for (const v of ordered) {
    if (isProgressVideoDone(v)) done.push(v);
    else active.push(v);
  }
  return { active, done };
}

function canManualSend(v, taskStatus) {
  if (!["running", "paused"].includes(taskStatus)) return false;
  if (v.status === "posting") {
    const chs = v.channels || [];
    return chs.length > 0 && !chs.every((c) => c.status === "done");
  }
  if (["downloading", "done"].includes(v.status)) return false;
  return true;
}

function sendBtnLabel(v) {
  return v.status === "failed" || v.status === "skipped" ? "重试" : "发送";
}

function progressMessageHtml(v) {
  if (v.message) return escapeHtml(v.message);
  if (v.status === "failed") return "发送失败，可点击重试";
  return "";
}

function buildProgressCardHtml(v, i, taskStatus) {
  const statusLabel = VIDEO_STATUS[v.status] || v.status;
  const showSend = canManualSend(v, taskStatus);
  const sending = state.sendingVideos.has(v.id);
  const sendBtn = showSend
    ? `<button type="button" class="btn-send-video" data-send-video="${escapeAttr(v.id)}"${sending ? " disabled" : ""}>${sending ? "…" : sendBtnLabel(v)}</button>`
    : "";
  return `
    <div class="progress-card status-${v.status}" data-video-id="${escapeAttr(v.id)}">
      <div class="progress-body">
        <div class="progress-head">
          <span class="progress-num">${i + 1}</span>
          <span class="progress-title">${escapeHtml(v.title)}</span>
          ${sendBtn}
          <span class="video-status-badge status-${v.status}">${statusLabel}</span>
        </div>
        ${videoTimeHtml(v)}
        <div class="progress-account">${v.account ? `账号: ${escapeHtml(v.account)}` : ""}</div>
        <div class="progress-msg">${progressMessageHtml(v)}</div>
        <div class="channel-tags">${channelTagsHtml(v.channels)}</div>
      </div>
    </div>`;
}

function renderVideoProgressFull(videos, taskStatus, task = state.taskDetail) {
  const list = $("#videoProgressList");
  const doneWrap = $("#videoProgressDoneWrap");
  const doneList = $("#videoProgressDoneList");
  const doneLabel = $("#doneVideosToggleLabel");
  const toggleBtn = $("#toggleDoneVideosBtn");

  if (!videos.length) {
    list.innerHTML = '<div class="empty-hint">暂无视频</div>';
    doneWrap?.classList.add("hidden");
    state.progressFp = "";
    list.dataset.fp = "";
    return;
  }

  const { active, done } = splitProgressForDisplay(videos, task);

  list.innerHTML = active.length
    ? active.map((v, i) => buildProgressCardHtml(v, i, taskStatus)).join("")
    : '<div class="empty-hint">暂无待发送视频</div>';

  if (doneWrap && doneList && doneLabel && toggleBtn) {
    if (done.length) {
      doneWrap.classList.remove("hidden");
      doneLabel.textContent = `已发完 ${done.length} 条`;
      toggleBtn.setAttribute("aria-expanded", state.doneVideosExpanded ? "true" : "false");
      toggleBtn.classList.toggle("expanded", state.doneVideosExpanded);
      doneList.classList.toggle("hidden", !state.doneVideosExpanded);
      doneList.innerHTML = state.doneVideosExpanded
        ? done.map((v, i) => buildProgressCardHtml(v, i, taskStatus)).join("")
        : "";
    } else {
      doneWrap.classList.add("hidden");
      doneList.innerHTML = "";
      doneList.classList.add("hidden");
    }
  }

  const sortKey = `${task?.source || ""}|${task?.search_sort || ""}|${task?.platform || ""}`;
  const expanded = state.doneVideosExpanded ? "1" : "0";
  state.progressFp = `${progressFingerprint(videos)}|${taskStatus || ""}|${expanded}|${sortKey}`;
  list.dataset.fp = state.progressFp;
}

function patchProgressCard(card, v, taskStatus) {
  card.className = `progress-card status-${v.status}`;
  const badge = card.querySelector(".video-status-badge");
  if (badge) {
    badge.textContent = VIDEO_STATUS[v.status] || v.status;
    badge.className = `video-status-badge status-${v.status}`;
  }
  const showSend = canManualSend(v, taskStatus);
  const sending = state.sendingVideos.has(v.id);
  let btn = card.querySelector(".btn-send-video");
  if (showSend) {
    if (!btn && badge) {
      badge.insertAdjacentHTML(
        "beforebegin",
        `<button type="button" class="btn-send-video" data-send-video="${escapeAttr(v.id)}">${sendBtnLabel(v)}</button>`
      );
      btn = card.querySelector(".btn-send-video");
    }
    if (btn) {
      btn.disabled = sending;
      btn.textContent = sending ? "…" : sendBtnLabel(v);
    }
  } else if (btn) {
    btn.remove();
  }
  const timeEl = card.querySelector(".progress-time");
  if (timeEl) {
    if (v.sent_at) timeEl.textContent = `发送时间: ${v.sent_at}`;
    else if (v.started_at) timeEl.textContent = `开始时间: ${v.started_at}`;
    else timeEl.textContent = "";
  }
  const account = card.querySelector(".progress-account");
  if (account) account.textContent = v.account ? `账号: ${v.account}` : "";
  const msg = card.querySelector(".progress-msg");
  if (msg) msg.textContent = v.message || "";
  const tags = card.querySelector(".channel-tags");
  if (tags) tags.innerHTML = channelTagsHtml(v.channels);
}

function updateVideoProgress(
  videos,
  force = false,
  taskStatus = state.taskDetail?.status,
  task = state.taskDetail,
) {
  const expanded = state.doneVideosExpanded ? "1" : "0";
  const sortKey = `${task?.source || ""}|${task?.search_sort || ""}|${task?.platform || ""}`;
  const fp = `${progressFingerprint(videos)}|${taskStatus || ""}|${expanded}|${sortKey}`;

  if (!force && fp === state.progressFp) return;

  renderVideoProgressFull(videos, taskStatus, task);
}

async function sendVideoManual(videoId) {
  if (!state.selectedTaskId || state.sendingVideos.has(videoId)) return;
  state.sendingVideos.add(videoId);
  if (state.taskDetail) {
    updateVideoProgress(state.taskDetail.video_progress || [], true, state.taskDetail.status, state.taskDetail);
  }
  try {
    await api(
      `/api/tasks/${state.selectedTaskId}/videos/${encodeURIComponent(videoId)}/send`,
      { method: "POST" }
    );
    await pollDetail();
  } catch (e) {
    alert(`发送失败: ${e.message}`);
  } finally {
    state.sendingVideos.delete(videoId);
    if (state.taskDetail) {
      updateVideoProgress(state.taskDetail.video_progress || [], true, state.taskDetail.status, state.taskDetail);
    }
  }
}

async function startTask() {
  if (!state.selectedTaskId) return;
  try {
    await api(`/api/tasks/${state.selectedTaskId}/start`, { method: "POST" });
    await pollDetail();
  } catch (e) {
    alert(`启动失败: ${e.message}`);
  }
}

async function pauseTask() {
  if (!state.selectedTaskId) return;
  const btn = $("#pauseTaskBtn");
  btn.disabled = true;
  try {
    await api(`/api/tasks/${state.selectedTaskId}/pause`, { method: "POST" });
    if (state.taskDetail) {
      state.taskDetail.status = "paused";
      for (const vp of state.taskDetail.video_progress || []) {
        if (["waiting", "downloading", "posting"].includes(vp.status)) {
          vp.status = "pending";
          vp.message = "已暂停";
        }
      }
      renderTaskDetail(true);
    }
    await pollDetail();
  } catch (e) {
    alert(`暂停失败: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function copyTaskLink() {
  const taskId = state.taskDetail?.task_id || state.selectedTaskId;
  if (!taskId) return;
  const link = getTaskShareUrl(taskId);
  try {
    await navigator.clipboard.writeText(link);
    const btn = $("#copyTaskLinkBtn");
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = "已复制";
      setTimeout(() => { btn.textContent = prev; }, 1500);
    }
  } catch {
    prompt("复制任务链接：", link);
  }
}

async function deleteTask(taskId = null) {
  const id = typeof taskId === "string" && taskId ? taskId : getActiveTaskId();
  if (!id) {
    alert("请先选择要删除的任务");
    return;
  }

  const task = state.tasks.find((t) => t.task_id === id) || state.taskDetail;
  const name = task?.name || "此任务";
  const msg = task?.status === "running"
    ? `任务「${name}」正在运行，删除后将停止执行。确认删除？`
    : `确认删除任务「${name}」？`;
  if (!confirm(msg)) return;

  const delBtn = $("#deleteTaskBtn");
  delBtn.disabled = true;

  // 乐观更新：先从 UI 移除
  state.tasks = state.tasks.filter((t) => t.task_id !== id);
  clearTaskView();
  renderSidebar();

  try {
    await api(`/api/tasks/${id}`, { method: "DELETE" });
  } catch (e) {
    alert(`删除失败: ${e.message}`);
  } finally {
    delBtn.disabled = false;
    await refreshTasks();
  }
}

// ── Dialog ──

function isDialogCustomMode() {
  return dialog.tab === "douyin" && dialog.douyinSubTab === "links";
}

function isDialogCollectionMode() {
  return dialog.tab === "douyin" && dialog.douyinSubTab === "collection";
}

function updateRecurringButtonLabel() {
  const btn = $("#dialogCreateRecurring");
  if (!btn) return;
  const isEdit = dialog.mode === "edit";
  const prefix = isEdit ? "保存" : "创建";
  if (isDialogCollectionMode()) {
    btn.textContent = `${prefix}长期收藏任务`;
  } else {
    btn.textContent = `${prefix}长期任务`;
  }
}

function setDialogMode(mode) {
  dialog.mode = mode;
  const isEdit = mode === "edit";
  $("#dialogTitle").textContent = isEdit ? "修改任务" : "新建任务";
  $("#dialogCreate").textContent = isEdit ? "保存任务" : "创建任务";
  document.querySelector(".dialog-main-tabs")?.classList.toggle("hidden", isEdit);
  updateRecurringButtonLabel();
}

function setDouyinSubTab(subTab) {
  dialog.douyinSubTab = subTab;
  $$("[data-douyin-subtab]").forEach((t) => {
    t.classList.toggle("active", t.dataset.douyinSubtab === subTab);
  });
  const isKeyword = subTab === "keyword";
  const isCollection = subTab === "collection";
  const isLinks = subTab === "links";
  $("#searchSection")?.classList.toggle("hidden", !isKeyword);
  $("#collectionSection")?.classList.toggle("hidden", !isCollection);
  const isEdit = dialog.mode === "edit";
  $("#customLinksSection")?.classList.toggle("hidden", !isLinks || isEdit);
  const title = $("#videoSelectTitle");
  const hint = $("#videoSelectHint");
  if (title) {
    if (isLinks) {
      title.textContent = "解析结果";
      if (hint) hint.textContent = "可选，也可创建空任务稍后追加";
    } else if (isCollection) {
      title.textContent = "收藏列表";
      if (hint) hint.textContent = "点击选择/取消，默认全选";
    } else {
      title.textContent = "搜索结果";
      if (hint) hint.textContent = "点击选择/取消，默认全选";
    }
  }
  $("#dialogCreateRecurring")?.classList.toggle("hidden", isLinks);
  updateRecurringButtonLabel();
  if (isCollection) {
    loadDouyinAccountOptions();
  }
  if (isLinks && dialog.mode === "create" && !dialog.videos.length) {
    $("#dialogVideoList").innerHTML = '<div class="empty-hint">可选：粘贴链接后解析，也可直接创建空任务</div>';
  } else if (isKeyword && dialog.mode === "create" && !dialog.videos.length) {
    $("#dialogVideoList").innerHTML = '<div class="empty-hint">搜索后选择视频</div>';
  } else if (isCollection && dialog.mode === "create" && !dialog.videos.length) {
    $("#dialogVideoList").innerHTML = '<div class="empty-hint">选择账号后点击「加载收藏」</div>';
  }
  updateDialogPlatformUI();
  updateDialogBtns();
}

function setDialogTab(tab) {
  dialog.tab = tab;
  dialog.platform = tab === "bili" ? "bili" : "douyin";
  $$("[data-dialog-tab]").forEach((t) => {
    t.classList.toggle("active", t.dataset.dialogTab === tab);
  });
  const isDouyin = tab === "douyin";
  $("#douyinSubTabs")?.classList.toggle("hidden", !isDouyin);
  if (isDouyin) {
    setDouyinSubTab(dialog.douyinSubTab || "keyword");
  } else {
    $("#searchSection")?.classList.remove("hidden");
    $("#collectionSection")?.classList.add("hidden");
    $("#customLinksSection")?.classList.add("hidden");
    const title = $("#videoSelectTitle");
    const hint = $("#videoSelectHint");
    if (title) title.textContent = "搜索结果";
    if (hint) hint.textContent = "点击选择/取消，默认全选";
    $("#dialogCreateRecurring")?.classList.remove("hidden");
    updateDialogPlatformUI();
    updateDialogBtns();
  }
}

function openDialog() {
  dialog.mode = "create";
  dialog.tab = "bili";
  dialog.douyinSubTab = "keyword";
  dialog.douyinCookieIndex = 0;
  dialog.collectionCursor = 0;
  dialog.collectionHasMore = false;
  dialog.editTaskId = null;
  dialog.lockedVideoIds = new Set();
  dialog.platform = "bili";
  dialog.videos = [];
  dialog.channelFilter = "all";
  dialog.selectedChannels = new Set();
  dialog.selectedVideos = new Set();
  dialog.selectedAccounts = new Set(state.accounts.map((a) => a.id));
  dialog.cron = "";
  dialog.searchSort = "recent";
  $("#dialogCustomLinks").value = "";
  $("#dialogKeyword").value = "";
  $("#dialogBiliPages").value = "1";
  setDialogSearchSort("recent");
  applyScheduleToDialog(null);
  $("#dialogSearchStatus").classList.add("hidden");
  $("#dialogVideoList").innerHTML = '<div class="empty-hint">搜索后选择视频</div>';
  $$("[data-dialog-tab]").forEach((t) => {
    t.classList.toggle("active", t.dataset.dialogTab === "bili");
  });
  $$("[data-dialog-cat]").forEach((c) => {
    c.classList.toggle("active", c.dataset.dialogCat === "all");
  });
  renderDialogChannels();
  renderDialogAccounts();
  setDialogMode("create");
  setDialogTab("bili");
  updateDialogBtns();
  $("#taskDialog").classList.remove("hidden");
}

function openEditDialog() {
  const t = state.taskDetail;
  if (!t) return;
  if (t.status === "running") {
    alert("请先暂停任务后再修改");
    return;
  }

  dialog.mode = "edit";
  dialog.editTaskId = t.task_id;
  const isCustom = t.task_type === "custom" || t.source === "manual";
  const isCollection = t.source === "collection";
  const isDouyinSearch = t.platform === "douyin" && !isCustom && !isCollection;
  dialog.tab = isCustom || isCollection || isDouyinSearch ? "douyin" : (t.platform || "bili");
  dialog.douyinSubTab = isCustom ? "links" : (isCollection ? "collection" : (isDouyinSearch ? "keyword" : "keyword"));
  dialog.douyinCookieIndex = isCollection ? (t.douyin_cookie_index ?? 0) : 0;
  dialog.lockedVideoIds = new Set(
    (t.video_progress || [])
      .filter((vp) => vp.status === "done" || vp.status === "skipped")
      .map((vp) => vp.id)
  );
  dialog.platform = t.platform || "bili";
  dialog.channelFilter = "all";
  dialog.selectedChannels = new Set((t.channels || []).map((ch) => channelKey(ch)));
  dialog.selectedAccounts = new Set(t.account_ids || []);

  const payloadVideos = (t.videos || []).map((v) => ({
    ...v,
    platform: v.platform || t.platform,
  }));
  if (payloadVideos.length) {
    dialog.videos = payloadVideos;
  } else {
    dialog.videos = (t.video_progress || []).map((vp) => ({
      id: vp.id,
      title: vp.title,
      pic: vp.pic || "",
      author: vp.author || "",
      link: "",
      play_addr: "",
      platform: t.platform,
    }));
  }

  dialog.selectedVideos = new Set(dialog.videos.map((v) => v.id));

  $("#dialogKeyword").value = t.keyword || "";
  $("#dialogBiliPages").value = "1";
  setDialogSearchSort(t.search_sort || "recent");
  applyScheduleToDialog(t);
  $("#dialogSearchStatus").classList.add("hidden");

  $$("[data-dialog-tab]").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.dialogTab === dialog.tab);
  });
  $$("[data-dialog-cat]").forEach((c) => {
    c.classList.toggle("active", c.dataset.dialogCat === "all");
  });

  renderDialogChannels();
  renderDialogAccounts();
  renderDialogVideos();
  setDialogMode("edit");
  setDialogTab(dialog.tab);
  updateDialogBtns();
  $("#taskDialog").classList.remove("hidden");
}

function updateDialogPlatformUI() {
  const isBili = dialog.platform === "bili";
  $("#dialogBiliPagesWrap").classList.toggle("hidden", !isBili);
}

function getDialogSearchSort() {
  const v = $("#dialogSearchSort")?.value;
  return v === "default" ? "default" : "recent";
}

function setDialogSearchSort(sort) {
  const value = sort === "default" ? "default" : "recent";
  dialog.searchSort = value;
  const el = $("#dialogSearchSort");
  if (el) el.value = value;
}

function closeDialog() {
  $("#taskDialog").classList.add("hidden");
}

function openFilterDialog() {
  loadFilterPatterns();
  $("#filterDialog").classList.remove("hidden");
}

function closeFilterDialog() {
  $("#filterDialog").classList.add("hidden");
}

async function loadFilterPatterns() {
  try {
    const data = await api("/api/filter-patterns");
    $("#filterPatternsInput").value = (data.patterns || []).join("\n");
  } catch (e) {
    $("#filterPatternsInput").value = "";
    alert(`加载过滤词失败: ${e.message}`);
  }
}

function parseFilterPatternsText(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
}

async function saveFilterPatterns() {
  const patterns = parseFilterPatternsText($("#filterPatternsInput").value);
  const btn = $("#filterSaveBtn");
  btn.disabled = true;
  try {
    await api("/api/filter-patterns", {
      method: "PUT",
      body: JSON.stringify({ patterns }),
    });
    closeFilterDialog();
  } catch (e) {
    alert(`保存失败: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

function renderDialogChannels() {
  const container = $("#dialogChannelBtns");
  const visible = state.channels.filter(
    (ch) => dialog.channelFilter === "all" || ch.category === dialog.channelFilter
  );

  if (!visible.length) {
    container.innerHTML = '<div class="empty-hint">无可用频道</div>';
    return;
  }

  container.innerHTML = visible
    .map((ch) => {
      const key = channelKey(ch);
      const active = dialog.selectedChannels.has(key);
      return `
        <button type="button" class="toggle-btn${active ? " active" : ""}"
                data-key="${key}"
                data-guild="${ch.guild_id}"
                data-channel="${ch.channel_id}"
                data-name="${escapeAttr(ch.name)}">
          ${escapeHtml(ch.name)}
        </button>`;
    })
    .join("");
}

function toggleDialogChannel(key) {
  if (dialog.selectedChannels.has(key)) {
    dialog.selectedChannels.delete(key);
  } else {
    dialog.selectedChannels.add(key);
  }
  renderDialogChannels();
  updateDialogBtns();
}

function renderDialogAccounts() {
  const qq = state.accounts.filter((a) => a.type === "qq");
  const bot = state.accounts.filter((a) => a.type === "bot");
  const render = (accounts) =>
    accounts.map((a) => {
      const checked = dialog.selectedAccounts.has(a.id);
      return `
      <label class="check-item">
        <input type="checkbox" data-account="${a.id}"${checked ? " checked" : ""} />
        <span>${escapeHtml(a.name)}</span>
      </label>`;
    }).join("");
  $("#dialogQqAccounts").innerHTML = render(qq);
  $("#dialogBotAccounts").innerHTML = render(bot);
}

function setDialogAccount(id, checked) {
  if (checked) {
    dialog.selectedAccounts.add(id);
  } else {
    dialog.selectedAccounts.delete(id);
  }
}

function getDialogAccounts() {
  return [...dialog.selectedAccounts];
}

function getDialogChannels() {
  return state.channels
    .filter((ch) => dialog.selectedChannels.has(channelKey(ch)))
    .map((ch) => ({
      guild_id: ch.guild_id,
      channel_id: ch.channel_id,
      name: ch.name,
    }));
}

function getSelectedDialogVideos() {
  return dialog.videos.filter(
    (v) => dialog.selectedVideos.has(v.id) || dialog.lockedVideoIds.has(v.id)
  );
}

function renderDialogVideos() {
  const list = $("#dialogVideoList");
  if (!dialog.videos.length) {
    list.innerHTML = '<div class="empty-hint">搜索后选择视频</div>';
    return;
  }
  list.innerHTML = dialog.videos
    .map((v, i) => {
      const locked = dialog.lockedVideoIds.has(v.id);
      const active = dialog.selectedVideos.has(v.id) || locked;
      const lockMark = locked ? '<span class="video-lock" title="已发送，不可取消">✓</span>' : "";
      return `
        <button type="button" class="video-select-item${active ? " active" : ""}${locked ? " locked" : ""}" data-id="${escapeAttr(v.id)}"${locked ? " disabled" : ""}>
          <span class="queue-num">${i + 1}</span>
          <span class="video-title">${escapeHtml(v.title)}</span>
          ${lockMark}
        </button>`;
    })
    .join("");
}

function toggleDialogVideo(id) {
  if (dialog.lockedVideoIds.has(id)) return;
  if (dialog.selectedVideos.has(id)) {
    dialog.selectedVideos.delete(id);
  } else {
    dialog.selectedVideos.add(id);
  }
  renderDialogVideos();
  updateDialogBtns();
}

function updateDialogBtns() {
  const hasChannels = dialog.selectedChannels.size > 0;
  const hasAccounts = dialog.selectedAccounts.size > 0;
  const hasKeyword = $("#dialogKeyword").value.trim().length > 0;
  const hasVideos = dialog.selectedVideos.size > 0;
  const isCustom = isDialogCustomMode();
  const isCollection = isDialogCollectionMode();
  const baseOk = hasChannels && hasAccounts && (isCustom || hasVideos);
  $("#dialogCreate").disabled = !baseOk;
  const recurringOk = hasChannels && hasAccounts && hasVideos && (hasKeyword || isCollection) && !isCustom;
  $("#dialogCreateRecurring").disabled = !recurringOk;

  const hint = $("#dialogFooterHint");
  if (!hint) return;
  if (!hasChannels || !hasAccounts) {
    hint.textContent = "请选择目标频道与发送账号";
  } else if (!isCustom && !hasVideos) {
    hint.textContent = isCollection ? "请加载并选择收藏视频" : "请搜索并选择视频";
  } else if (!isCustom && !recurringOk && (hasKeyword || isCollection)) {
    hint.textContent = "长期任务需有关键词或收藏来源，且至少选中一个视频";
  } else {
    hint.textContent = "配置完成，可创建任务";
  }
}

function getDialogCollectionAccountLabel() {
  const idx = dialog.douyinCookieIndex ?? 0;
  const acc = (dialog.douyinAccounts || []).find((a) => a.index === idx);
  if (acc?.nickname) return acc.nickname;
  if (acc?.label && acc.label !== "加载中…") return acc.label;
  const select = $("#dialogDouyinAccount");
  const opt = select?.selectedOptions?.[0];
  if (!opt?.value || opt.value === "") return "";
  const text = (opt.textContent || "").split(" (")[0].trim();
  if (!text || text === "加载中…" || text.startsWith("请先在设置")) return "";
  return text;
}

async function loadDouyinAccountOptions() {
  const select = $("#dialogDouyinAccount");
  if (!select) return;
  select.innerHTML = '<option value="">加载中…</option>';
  try {
    const data = await api("/api/douyin/cookie-accounts");
    const accounts = data.accounts || [];
    dialog.douyinAccounts = accounts;
    if (!accounts.length) {
      select.innerHTML = '<option value="">请先在设置页配置抖音 Cookie</option>';
      return;
    }
    select.innerHTML = accounts.map((a) => {
      const label = a.nickname ? `${a.label} (${a.index + 1})` : a.label;
      const err = a.error ? ` — ${a.error}` : "";
      return `<option value="${a.index}">${escapeHtml(label + err)}</option>`;
    }).join("");
    const idx = String(dialog.douyinCookieIndex ?? 0);
    if ([...select.options].some((o) => o.value === idx)) {
      select.value = idx;
    }
  } catch (e) {
    select.innerHTML = `<option value="">${escapeHtml(e.message)}</option>`;
  }
}

async function dialogFetchCollection(reset = true) {
  const select = $("#dialogDouyinAccount");
  const cookieIndex = select?.value === "" ? 0 : Number(select?.value || 0);
  dialog.douyinCookieIndex = cookieIndex;
  const status = $("#dialogSearchStatus");
  const btn = $("#dialogCollectionBtn");
  const moreBtn = $("#dialogCollectionMoreBtn");
  if (btn?.disabled) return;

  const cursor = reset ? 0 : dialog.collectionCursor;
  status.className = "status-bar loading";
  status.textContent = reset ? "正在加载收藏…" : "正在加载更多…";
  status.classList.remove("hidden");
  btn.disabled = true;
  if (moreBtn) moreBtn.disabled = true;
  const allBtn = $("#dialogCollectionAllBtn");
  if (allBtn) allBtn.disabled = true;

  try {
    const data = await api("/api/douyin/collection", {
      method: "POST",
      body: JSON.stringify({ cookie_index: cookieIndex, cursor, count: 20 }),
    });
    const videos = data.videos || [];
    if (reset) {
      dialog.videos = videos;
      dialog.selectedVideos = new Set(videos.map((v) => v.id));
    } else {
      const seen = new Set(dialog.videos.map((v) => v.id));
      for (const v of videos) {
        if (!seen.has(v.id)) {
          dialog.videos.push(v);
          dialog.selectedVideos.add(v.id);
          seen.add(v.id);
        }
      }
    }
    dialog.collectionCursor = data.cursor ?? cursor;
    dialog.collectionHasMore = !!data.has_more;
    renderDialogVideos();
    updateDialogBtns();
    status.className = "status-bar success";
    status.textContent = `已加载 ${dialog.videos.length} 条收藏${data.has_more ? "，可继续加载更多" : ""}`;
    moreBtn?.classList.toggle("hidden", !data.has_more);
  } catch (e) {
    status.className = "status-bar error";
    status.textContent = e.message;
    if (reset) {
      dialog.videos = [];
      dialog.selectedVideos = new Set();
      renderDialogVideos();
      updateDialogBtns();
    }
    moreBtn?.classList.add("hidden");
  } finally {
    btn.disabled = false;
    if (moreBtn) moreBtn.disabled = false;
    $("#dialogCollectionAllBtn") && ($("#dialogCollectionAllBtn").disabled = false);
  }
}

async function dialogFetchCollectionAll() {
  const select = $("#dialogDouyinAccount");
  const cookieIndex = select?.value === "" ? 0 : Number(select?.value || 0);
  dialog.douyinCookieIndex = cookieIndex;
  const status = $("#dialogSearchStatus");
  const btn = $("#dialogCollectionAllBtn");
  const loadBtn = $("#dialogCollectionBtn");
  const moreBtn = $("#dialogCollectionMoreBtn");
  if (btn?.disabled) return;

  status.className = "status-bar loading";
  status.textContent = "正在加载全部收藏（可能需要几十秒）…";
  status.classList.remove("hidden");
  btn.disabled = true;
  if (loadBtn) loadBtn.disabled = true;
  if (moreBtn) moreBtn.disabled = true;

  try {
    const data = await api("/api/douyin/collection", {
      method: "POST",
      body: JSON.stringify({
        cookie_index: cookieIndex,
        fetch_all: true,
        max_items: 2000,
        count: 20,
      }),
    });
    const videos = data.videos || [];
    dialog.videos = videos;
    dialog.selectedVideos = new Set(videos.map((v) => v.id));
    dialog.collectionCursor = data.cursor ?? 0;
    dialog.collectionHasMore = !!data.has_more;
    renderDialogVideos();
    updateDialogBtns();
    status.className = "status-bar success";
    const truncated = data.truncated ? "（已达上限，可分批创建多个任务）" : "";
    const pages = data.pages_fetched ? `，共 ${data.pages_fetched} 页` : "";
    const partial = data.error ? `（后续翻页中断: ${data.error}）` : "";
    status.textContent = `已加载 ${dialog.videos.length} 条收藏${pages}${truncated}${partial}`;
    moreBtn?.classList.toggle("hidden", !data.has_more);
  } catch (e) {
    status.className = "status-bar error";
    status.textContent = e.message;
    dialog.videos = [];
    dialog.selectedVideos = new Set();
    renderDialogVideos();
    updateDialogBtns();
    moreBtn?.classList.add("hidden");
  } finally {
    btn.disabled = false;
    if (loadBtn) loadBtn.disabled = false;
    if (moreBtn) moreBtn.disabled = false;
  }
}

async function dialogParseLinks() {
  const text = $("#dialogCustomLinks")?.value?.trim();
  if (!text) {
    alert("请先粘贴抖音分享文本");
    return;
  }
  const status = $("#dialogSearchStatus");
  const btn = $("#dialogParseLinksBtn");
  status.className = "status-bar loading";
  status.textContent = "正在解析链接…";
  status.classList.remove("hidden");
  btn.disabled = true;

  try {
    const data = await api("/api/douyin/parse-links", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const videos = data.videos || [];
    const errors = data.errors || [];
    if (dialog.mode === "edit") {
      const seen = new Set(dialog.videos.map((v) => v.id));
      for (const v of videos) {
        if (!seen.has(v.id)) {
          dialog.videos.push(v);
          seen.add(v.id);
        }
      }
    } else {
      dialog.videos = videos;
    }
    dialog.selectedVideos = new Set(dialog.videos.map((v) => v.id));
    renderDialogVideos();
    const errHint = errors.length ? `，${errors.length} 条解析失败` : "";
    status.className = videos.length ? "status-bar success" : "status-bar error";
    status.textContent = videos.length
      ? `解析完成：${videos.length} 条视频${errHint}`
      : `未解析到有效视频${errHint}`;
  } catch (e) {
    status.className = "status-bar error";
    status.textContent = e.message;
    dialog.videos = [];
    dialog.selectedVideos = new Set();
    renderDialogVideos();
  } finally {
    btn.disabled = false;
    updateDialogBtns();
  }
}

function openAppendLinksDialog() {
  const t = state.taskDetail;
  if (!t || t.task_type !== "custom") return;
  $("#appendLinksText").value = "";
  const status = $("#appendLinksStatus");
  status.classList.add("hidden");
  status.textContent = "";
  $("#appendLinksDialog").classList.remove("hidden");
}

function closeAppendLinksDialog() {
  $("#appendLinksDialog").classList.add("hidden");
}

async function submitAppendLinks() {
  const t = state.taskDetail;
  if (!t) return;
  const text = $("#appendLinksText")?.value?.trim();
  if (!text) {
    alert("请粘贴分享文本");
    return;
  }
  const btn = $("#appendLinksConfirm");
  const status = $("#appendLinksStatus");
  btn.disabled = true;
  status.className = "status-bar loading";
  status.textContent = "解析并追加中…";
  status.classList.remove("hidden");

  try {
    const data = await api(`/api/tasks/${t.task_id}/append-links`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const errCount = (data.errors || []).length;
    status.className = "status-bar success";
    status.textContent = `已追加 ${data.added || 0} 条${data.duplicate ? `（跳过 ${data.duplicate} 条重复）` : ""}${errCount ? `，${errCount} 条失败` : ""}`;
    await refreshTasks();
    await selectTask(t.task_id, { updateUrl: false });
    if (data.added > 0) {
      setTimeout(closeAppendLinksDialog, 800);
    }
  } catch (e) {
    status.className = "status-bar error";
    status.textContent = e.message;
  } finally {
    btn.disabled = false;
  }
}

async function syncTaskCollection() {
  const t = state.taskDetail;
  if (!t || t.source !== "collection") return;
  const btn = $("#syncCollectionBtn");
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  const prevText = btn.textContent;
  btn.textContent = "同步中…";
  const countBefore = t.video_count || 0;

  try {
    const data = await api(`/api/tasks/${t.task_id}/sync-collection`, { method: "POST" });
    let msg;
    if (data.added > 0) {
      msg = `已同步 ${data.added} 条新收藏（共 ${data.video_count} 条）`;
      if (data.stopped_on_duplicate) msg += "，已与已有收藏衔接";
    } else {
      msg = "暂无新收藏，已与收藏夹同步";
    }
    if (data.resume_error) msg += `\n${data.resume_error}`;
    else if (data.warning) msg += `\n（${data.warning}）`;
    alert(msg);
    await refreshTasks();
    await selectTask(t.task_id, { updateUrl: false });
  } catch (e) {
    await refreshTasks();
    await selectTask(t.task_id, { updateUrl: false });
    const after = state.taskDetail?.video_count || 0;
    if (after > countBefore) {
      alert(`同步可能部分成功：已新增约 ${after - countBefore} 条视频，但接口报错：${e.message}\n请查看下方运行日志确认。`);
    } else {
      alert(`同步失败: ${e.message}`);
    }
  } finally {
    btn.disabled = false;
    btn.textContent = prevText;
  }
}

async function dialogSearch() {
  const keyword = $("#dialogKeyword").value.trim();
  if (!keyword) return;

  const status = $("#dialogSearchStatus");
  const btn = $("#dialogSearchBtn");
  if (btn.disabled) return;

  status.className = "status-bar loading";
  status.textContent = "搜索中...";
  status.classList.remove("hidden");
  btn.disabled = true;

  const body = { platform: dialog.platform, keyword, search_sort: getDialogSearchSort() };
  if (dialog.platform === "bili") {
    body.bili_pages = +$("#dialogBiliPages").value || 1;
  }

  try {
    const data = await api("/api/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (dialog.mode === "edit") {
      const seen = new Set(dialog.videos.map((v) => v.id));
      for (const v of data.videos) {
        if (!seen.has(v.id)) {
          dialog.videos.push(v);
          dialog.selectedVideos.add(v.id);
          seen.add(v.id);
        }
      }
    } else {
      dialog.videos = data.videos;
      dialog.selectedVideos = new Set(data.videos.map((v) => v.id));
    }
    renderDialogVideos();
    updateDialogBtns();
    let msg = `找到 ${data.videos.length} 个视频，已默认全选`;
    if (data.raw_count != null && data.requested_limit) {
      msg += `（拉取 ${data.raw_count}/${data.requested_limit}`;
      if (data.pages_fetched != null && data.pages_requested) {
        msg += `，${data.pages_fetched}/${data.pages_requested} 页`;
      }
      msg += "）";
    }
    if (data.filtered_count > 0) msg += `，已过滤 ${data.filtered_count} 个`;
    if (data.pattern_errors?.length) msg += `（${data.pattern_errors.length} 条正则无效已忽略）`;
    if (data.warning) msg += `（${data.warning}）`;
    status.className = "status-bar";
    status.textContent = msg;
  } catch (e) {
    status.className = "status-bar error";
    status.textContent = e.message;
    dialog.videos = [];
    dialog.selectedVideos = new Set();
    renderDialogVideos();
    updateDialogBtns();
  } finally {
    btn.disabled = false;
  }
}

async function submitDialog(taskType) {
  const channels = getDialogChannels();
  const accountIds = getDialogAccounts();
  const keyword = $("#dialogKeyword").value.trim();
  const videos = getSelectedDialogVideos();
  const isCustom = taskType === "custom" || isDialogCustomMode();
  const isCollection = isDialogCollectionMode();

  if (!isCustom && !videos.length) { alert("请至少选择一个视频"); return; }
  if (taskType === "recurring" && !keyword && !isCollection) {
    alert("长期任务需要填写搜索关键词");
    return;
  }
  if (!channels.length) { alert("请至少选择一个频道"); return; }
  if (!accountIds.length) { alert("请至少选择一个发送账号"); return; }

  const btn = taskType === "recurring" ? $("#dialogCreateRecurring") : $("#dialogCreate");
  btn.disabled = true;

  let source = "search";
  if (isCustom) source = "manual";
  else if (isCollection) source = "collection";

  const body = {
    platform: isCustom || isCollection ? "douyin" : dialog.platform,
    keyword: isCustom || isCollection ? "" : keyword,
    task_type: taskType,
    source,
    douyin_cookie_index: isCollection ? dialog.douyinCookieIndex : 0,
    collection_account_label: isCollection
      ? (getDialogCollectionAccountLabel() || `账号 ${(dialog.douyinCookieIndex ?? 0) + 1}`)
      : "",
    videos: videos.map((v) => ({
      id: v.id,
      title: v.title,
      link: v.link || "",
      play_addr: v.play_addr || "",
      pic: v.pic || "",
      author: v.author || "",
      platform: isCustom ? "douyin" : (v.platform || dialog.platform),
    })),
    channels,
    account_ids: accountIds,
    search_sort: getDialogSearchSort(),
    ...readScheduleFromDialog(),
  };

  try {
    if (dialog.mode === "edit" && dialog.editTaskId) {
      await api(`/api/tasks/${dialog.editTaskId}`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      closeDialog();
      await refreshTasks();
      await selectTask(dialog.editTaskId);
      return;
    }

    const data = await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({ ...body, auto_start: true }),
    });
    closeDialog();
    await refreshTasks();
    await selectTask(data.task_id);
  } catch (e) {
    alert(`${dialog.mode === "edit" ? "保存" : "创建"}失败: ${e.message}`);
  } finally {
    updateDialogBtns();
  }
}

// ── Auto Like View ──

function alKey(guildId, channelId) {
  return `${String(guildId)}:${String(channelId)}`;
}

function channelKeysMatch(a, b) {
  return alKey(a?.guild_id, a?.channel_id) === alKey(b?.guild_id, b?.channel_id);
}

function getSelectedChannelConfig() {
  const key = settings.selectedChannelKey;
  if (!key) return null;
  const meta = state.channels.find((c) => alKey(c.guild_id, c.channel_id) === key);
  if (!meta) return null;
  const saved = (settings.autoLike.channels || []).find((c) => channelKeysMatch(c, meta));
  return {
    ...AUTO_LIKE_DEFAULTS,
    ...saved,
    guild_id: meta.guild_id,
    channel_id: meta.channel_id,
    name: meta.name,
  };
}

function getSavedChannelMap() {
  return new Map(
    (settings.autoLike.channels || []).map((c) => [alKey(c.guild_id, c.channel_id), c]),
  );
}

async function loadAutoLikeSettings() {
  const data = await api("/api/settings/auto-like");
  settings.autoLike = data.config || { enabled: false, channels: [] };
  settings.globalLogs = data.status?.logs || [];
  settings.runningChannels = new Set(data.status?.running || []);
}

function stopAutoLikePolling() {
  if (settings.pollTimer) {
    clearInterval(settings.pollTimer);
    settings.pollTimer = null;
  }
}

function startAutoLikePolling() {
  stopAutoLikePolling();
  settings.pollTimer = setInterval(async () => {
    if (state.mainView !== "auto-like") return;
    try {
      await flushAutoLikeSave();
      await loadAutoLikeSettings();
      renderAutoLikeMenu();
      const ch = getSelectedChannelConfig();
      if (ch?.enabled) {
        refreshAutoLikeRunningView(ch);
      } else if (!isAutoLikeFormFocused()) {
        renderAutoLikeDetail(false);
      } else if (ch) {
        renderAutoLikeRunLogs(ch);
      }
    } catch (e) {
      console.error("刷新自动点赞状态失败:", e);
    }
  }, 5000);
}

function showAutoLikeView() {
  hideAllMainPanels();
  state.mainView = "auto-like";
  $("#autoLikeView")?.classList.remove("hidden");
  renderSidebar();
  updateSidebarNav();
  renderAutoLikeMenu();
  renderAutoLikeDetail(true);
  startAutoLikePolling();
}

async function openAutoLikeView({ updateUrl = true } = {}) {
  try {
    await loadAutoLikeSettings();
    if (state.channels.length) {
      const keys = new Set(state.channels.map((c) => alKey(c.guild_id, c.channel_id)));
      if (!settings.selectedChannelKey || !keys.has(settings.selectedChannelKey)) {
        const first = state.channels[0];
        settings.selectedChannelKey = alKey(first.guild_id, first.channel_id);
      }
    } else {
      settings.selectedChannelKey = null;
    }
    showAutoLikeView();
    if (updateUrl && !isAutoLikeRoute()) {
      setAutoLikeUrl();
    }
  } catch (e) {
    console.error("加载自动点赞配置失败:", e);
    showAutoLikeView();
    if (updateUrl && !isAutoLikeRoute()) {
      setAutoLikeUrl();
    }
    const logsEl = $("#autoLikeRunLogs");
    if (logsEl) {
      logsEl.innerHTML = `<div class="log-line error">加载失败: ${escapeHtml(e.message)}</div>`;
    }
  }
}

function goBackFromAutoLike() {
  navigateToTasks();
}

function renderAutoLikeMenu() {
  const menu = $("#autoLikeChannelMenu");
  if (!menu) return;
  if (!state.channels.length) {
    menu.innerHTML = '<div class="auto-like-menu-empty">暂无频道</div>';
    return;
  }
  const savedMap = getSavedChannelMap();
  menu.innerHTML = state.channels
    .map((meta) => {
      const key = alKey(meta.guild_id, meta.channel_id);
      const saved = savedMap.get(key);
      const active = key === settings.selectedChannelKey ? " active" : "";
      const running = settings.runningChannels.has(key);
      const enabled = saved?.enabled ?? false;
      const badgeClass = running ? "running" : enabled ? "on" : "off";
      const badgeText = running ? "运行中" : enabled ? "已开启" : "未开启";
      return `
        <button type="button" class="auto-like-menu-item${active}" data-al-menu="${escapeAttr(key)}">
          <span class="al-menu-name">${escapeHtml(meta.name || key)}</span>
          <span class="al-menu-badge ${badgeClass}">${badgeText}</span>
        </button>`;
    })
    .join("");
}

function bindAutoLikeCronPicker(cron) {
  const mount = $("#autoLikeCronMount");
  if (!mount) return;
  const value = (cron ?? "").trim();
  if (autoLikeCronPicker) {
    autoLikeCronPicker.setValue(value);
    return;
  }
  autoLikeCronPicker = new CronPicker(mount, {
    value,
    allowEmpty: true,
    onChange: () => scheduleAutoSaveChannel(),
  });
}

function renderAutoLikeRunLogs(ch) {
  const el = $("#autoLikeRunLogs");
  if (!el || !ch) return;
  const key = alKey(ch.guild_id, ch.channel_id);
  const channelName = ch.name || key;
  const persisted = [...(ch.run_logs || [])].reverse();
  const live = (settings.globalLogs || []).filter(
    (l) => l.channel === channelName || l.channel === key,
  );
  const logs = persisted.length ? persisted : live;
  if (!logs.length) {
    el.innerHTML = '<div class="log-line">暂无运行日志</div>';
    return;
  }
  el.innerHTML = logs
    .map((l) => {
      const level = l.level && l.level !== "info" ? ` log-${l.level}` : "";
      const time = escapeHtml(l.time || "");
      const msg = escapeHtml(l.message || "");
      return `<div class="log-line${level}"><span class="log-time">${time}</span> ${msg}</div>`;
    })
    .join("");
}

function isChannelAutoLikeRunning(ch) {
  return !!ch?.enabled;
}

function buildChannelPayloadFromSaved(ch) {
  const accIds = ch.account_ids?.length ? ch.account_ids : state.accounts.map((a) => a.id);
  return {
    guild_id: ch.guild_id,
    channel_id: ch.channel_id,
    name: ch.name,
    enabled: !!ch.enabled,
    likes_min: ch.likes_min ?? AUTO_LIKE_DEFAULTS.likes_min,
    likes_max: ch.likes_max ?? AUTO_LIKE_DEFAULTS.likes_max,
    only_own_posts: ch.only_own_posts !== false,
    schedule_cron: ch.schedule_cron ?? "",
    account_ids: accIds,
    feeds_per_channel: ch.feeds_per_channel ?? AUTO_LIKE_DEFAULTS.feeds_per_channel,
  };
}

function renderAutoLikeRunInfo(ch) {
  const el = $("#autoLikeRunInfo");
  if (!el) return;
  const key = alKey(ch.guild_id, ch.channel_id);
  const taskRunning = settings.runningChannels.has(key);
  const accIds = ch.account_ids?.length ? ch.account_ids : state.accounts.map((a) => a.id);
  const accNames = state.accounts
    .filter((a) => accIds.includes(a.id))
    .map((a) => a.name)
    .join("、") || "全部账号";
  const lastRun = ch.last_run_at
    ? new Date(ch.last_run_at * 1000).toLocaleString("zh-CN")
    : "从未运行";
  const nextRun = ch.next_run_at
    ? new Date(ch.next_run_at * 1000).toLocaleString("zh-CN")
    : "-";
  el.innerHTML = `
    <div class="info-card"><label>状态</label><span>${taskRunning ? "正在执行" : "调度中"}</span></div>
    <div class="info-card"><label>点赞数</label><span>${ch.likes_min ?? 1} - ${ch.likes_max ?? 5}</span></div>
    <div class="info-card"><label>点赞时间</label><span><code>${escapeHtml(ch.schedule_cron || "-")}</code></span></div>
    <div class="info-card"><label>帖子范围</label><span>${ch.only_own_posts !== false ? "仅本系统账号" : "全部帖子"}</span></div>
    <div class="info-card"><label>下次运行</label><span>${escapeHtml(nextRun)}</span></div>
    <div class="info-card"><label>上次运行</label><span>${escapeHtml(lastRun)}</span></div>
    <div class="info-card wide"><label>参与账号</label><span>${escapeHtml(accNames)}</span></div>
    ${ch.last_run_message ? `<div class="info-card wide"><label>最近结果</label><span>${escapeHtml(ch.last_run_message)}</span></div>` : ""}
  `;
}

function refreshAutoLikeRunningView(ch) {
  if (!ch) return;
  setAutoLikeDetailMode(true);
  renderAutoLikeRunInfo(ch);
  renderAutoLikeRunLogs(ch);
  updateAutoLikeToggleButton(ch);
}

function setAutoLikeDetailMode(running) {
  $("#autoLikeEditForm")?.classList.toggle("hidden", running);
  $("#autoLikeRunView")?.classList.toggle("hidden", !running);
  $("#autoLikeDetailPanel")?.classList.toggle("running-mode", running);
  if (running) {
    autoLikeCronPicker = null;
    const mount = $("#autoLikeCronMount");
    if (mount) mount.innerHTML = "";
  }
}

function renderAutoLikeDetail(resetCron = true) {
  const empty = $("#autoLikeDetailEmpty");
  const panel = $("#autoLikeDetailPanel");
  const ch = getSelectedChannelConfig();
  if (!ch) {
    empty?.classList.remove("hidden");
    panel?.classList.add("hidden");
    return;
  }
  empty?.classList.add("hidden");
  panel?.classList.remove("hidden");

  $("#autoLikeDetailName").textContent = ch.name || settings.selectedChannelKey;
  const running = isChannelAutoLikeRunning(ch);

  if (running) {
    refreshAutoLikeRunningView(ch);
    return;
  }

  setAutoLikeDetailMode(false);

  $("#autoLikeChMin").value = ch.likes_min ?? AUTO_LIKE_DEFAULTS.likes_min;
  $("#autoLikeChMax").value = ch.likes_max ?? AUTO_LIKE_DEFAULTS.likes_max;
  $("#autoLikeChOwn").checked = ch.only_own_posts !== false;

  const accIds = ch.account_ids?.length ? ch.account_ids : state.accounts.map((a) => a.id);
  $("#autoLikeAccGrid").innerHTML = state.accounts
    .map((a) => {
      const checked = accIds.includes(a.id) ? "checked" : "";
      return `<label class="check-item"><input type="checkbox" data-al-acc value="${escapeAttr(a.id)}" ${checked} /> ${escapeHtml(a.name)}</label>`;
    })
    .join("");

  if (resetCron) {
    autoLikeCronPicker = null;
    const mount = $("#autoLikeCronMount");
    if (mount) mount.innerHTML = "";
    bindAutoLikeCronPicker(ch.schedule_cron ?? "");
  } else if (autoLikeCronPicker) {
    autoLikeCronPicker.setValue(ch.schedule_cron ?? "");
  }

  renderAutoLikeRunLogs(ch);
  updateAutoLikeToggleButton(ch);
}

function updateAutoLikeToggleButton(ch) {
  const btn = $("#autoLikeToggleBtn");
  if (!btn || !ch) return;
  const key = alKey(ch.guild_id, ch.channel_id);
  const running = settings.runningChannels.has(key);
  if (ch.enabled) {
    btn.textContent = running ? "运行中…" : "停止";
    btn.className = `btn btn-sm ${running ? "btn-secondary" : "btn-danger"}`;
    btn.disabled = running;
  } else {
    btn.textContent = "执行";
    btn.className = "btn btn-primary btn-sm";
    btn.disabled = false;
  }
}

function buildChannelPayloadFromForm() {
  const ch = getSelectedChannelConfig();
  if (!ch) return null;
  const accBoxes = document.querySelectorAll("#autoLikeAccGrid [data-al-acc]:checked");
  const likesMin = Math.max(1, parseInt($("#autoLikeChMin")?.value || "1", 10));
  const likesMax = Math.max(
    likesMin,
    parseInt($("#autoLikeChMax")?.value || String(likesMin), 10),
  );
  return {
    guild_id: ch.guild_id,
    channel_id: ch.channel_id,
    name: ch.name,
    enabled: !!ch.enabled,
    likes_min: likesMin,
    likes_max: likesMax,
    only_own_posts: $("#autoLikeChOwn")?.checked !== false,
    schedule_cron: (autoLikeCronPicker?.getValue() ?? ch.schedule_cron ?? "").trim(),
    account_ids: accBoxes.length ? [...accBoxes].map((el) => el.value) : [],
    feeds_per_channel: ch.feeds_per_channel ?? AUTO_LIKE_DEFAULTS.feeds_per_channel,
  };
}

function syncSelectedChannelFromForm() {
  const payload = buildChannelPayloadFromForm();
  if (!payload) return;
  const key = alKey(payload.guild_id, payload.channel_id);
  const list = settings.autoLike.channels || [];
  const idx = list.findIndex((c) => alKey(c.guild_id, c.channel_id) === key);
  if (idx >= 0) {
    list[idx] = { ...list[idx], ...payload };
  }
  settings.autoLike.channels = list;
}

function isAutoLikeFormFocused() {
  const panel = $("#autoLikeDetailPanel");
  return !!panel && !panel.classList.contains("hidden") && panel.contains(document.activeElement);
}

async function flushAutoLikeSave() {
  if (getSelectedChannelConfig()?.enabled) return;
  if (settings.saveTimer) {
    clearTimeout(settings.saveTimer);
    settings.saveTimer = null;
    await saveCurrentChannel();
  }
}

function scheduleAutoSaveChannel() {
  const ch = getSelectedChannelConfig();
  if (ch?.enabled) return;
  if (settings.saveTimer) clearTimeout(settings.saveTimer);
  settings.saveTimer = setTimeout(() => saveCurrentChannel(), 400);
}

async function saveCurrentChannel() {
  if (settings.saving || state.mainView !== "auto-like") return;
  const ch = getSelectedChannelConfig();
  if (ch?.enabled) return;
  const payload = buildChannelPayloadFromForm();
  if (!payload) return;
  settings.saving = true;
  try {
    const data = await api(
      `/api/settings/auto-like/channel/${payload.guild_id}/${payload.channel_id}`,
      { method: "PUT", body: JSON.stringify(payload) },
    );
    settings.autoLike = data.config || settings.autoLike;
    renderAutoLikeMenu();
  } catch (e) {
    console.error("保存频道配置失败:", e);
  } finally {
    settings.saving = false;
  }
}

async function toggleAutoLikeChannel() {
  await flushAutoLikeSave();
  const ch = getSelectedChannelConfig();
  if (!ch) return;
  const key = alKey(ch.guild_id, ch.channel_id);
  if (settings.runningChannels.has(key)) return;

  const payload = ch.enabled
    ? { ...buildChannelPayloadFromSaved(ch), enabled: false }
    : { ...buildChannelPayloadFromForm(), enabled: true };
  const btn = $("#autoLikeToggleBtn");
  if (btn) btn.disabled = true;
  try {
    const data = await api(
      `/api/settings/auto-like/channel/${payload.guild_id}/${payload.channel_id}`,
      { method: "PUT", body: JSON.stringify(payload) },
    );
    settings.autoLike = data.config || settings.autoLike;
    if (data.channel) {
      const key = alKey(data.channel.guild_id, data.channel.channel_id);
      settings.selectedChannelKey = key;
      const list = settings.autoLike.channels || [];
      const idx = list.findIndex((c) => channelKeysMatch(c, data.channel));
      if (idx >= 0) list[idx] = data.channel;
      else list.push(data.channel);
      settings.autoLike.channels = list;
    }
    renderAutoLikeMenu();
    renderAutoLikeDetail(true);
  } catch (e) {
    alert(`操作失败: ${e.message}`);
  } finally {
    const updated = getSelectedChannelConfig();
    if (updated) updateAutoLikeToggleButton(updated);
  }
}

function bindAutoLikeEvents() {
  $("#sidebarNavAutoLike")?.addEventListener("click", () => {
    if (state.mainView === "auto-like") return;
    openAutoLikeView();
  });

  $("#sidebarNavTasks")?.addEventListener("click", navigateToTasks);

  $("#autoLikeToggleBtn")?.addEventListener("click", toggleAutoLikeChannel);

  $("#autoLikeChannelMenu")?.addEventListener("click", async (e) => {
    const item = e.target.closest("[data-al-menu]");
    if (!item?.dataset.alMenu || item.dataset.alMenu === settings.selectedChannelKey) return;
    await flushAutoLikeSave();
    settings.selectedChannelKey = item.dataset.alMenu;
    renderAutoLikeMenu();
    renderAutoLikeDetail(true);
  });

  $("#autoLikeDetailPanel")?.addEventListener("change", (e) => {
    if (state.mainView !== "auto-like") return;
    if (getSelectedChannelConfig()?.enabled) return;
    scheduleAutoSaveChannel();
  });
  $("#autoLikeDetailPanel")?.addEventListener("input", (e) => {
    if (state.mainView !== "auto-like") return;
    if (getSelectedChannelConfig()?.enabled) return;
    if (e.target.matches("#autoLikeChMin, #autoLikeChMax")) scheduleAutoSaveChannel();
  });
}

function bindEvents() {
  bindAutoLikeEvents();
  bindSettingsEvents();

  $("#newTaskBtn").addEventListener("click", openDialog);
  $("#editTaskBtn").addEventListener("click", openEditDialog);
  $("#dialogFilterBtn").addEventListener("click", openFilterDialog);
  $("#closeFilterDialog").addEventListener("click", closeFilterDialog);
  $("#filterSaveBtn").addEventListener("click", saveFilterPatterns);
  $("#filterDialog").addEventListener("click", (e) => {
    if (e.target === $("#filterDialog")) closeFilterDialog();
  });
  $("#closeDialog").addEventListener("click", closeDialog);
  $("#dialogCancel").addEventListener("click", closeDialog);
  $("#taskDialog").addEventListener("click", (e) => {
    if (e.target === $("#taskDialog")) closeDialog();
  });

  $("#dialogKeyword").addEventListener("input", updateDialogBtns);

  bindCronInput();

  $("#dialogSearchBtn").addEventListener("click", dialogSearch);

  $$("[data-dialog-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const nextTab = tab.dataset.dialogTab;
      setDialogTab(nextTab);
      if (dialog.mode === "create") {
        dialog.videos = [];
        dialog.selectedVideos = new Set();
        dialog.collectionCursor = 0;
        dialog.collectionHasMore = false;
        $("#dialogCollectionMoreBtn")?.classList.add("hidden");
        if (nextTab === "douyin") {
          const sub = dialog.douyinSubTab || "keyword";
          if (sub === "links") {
            $("#dialogVideoList").innerHTML = '<div class="empty-hint">可选：粘贴链接后解析，也可直接创建空任务</div>';
          } else if (sub === "collection") {
            $("#dialogVideoList").innerHTML = '<div class="empty-hint">选择账号后点击「加载收藏」</div>';
          } else {
            $("#dialogVideoList").innerHTML = '<div class="empty-hint">搜索后选择视频</div>';
          }
        } else {
          $("#dialogVideoList").innerHTML = '<div class="empty-hint">搜索后选择视频</div>';
          $("#dialogCustomLinks").value = "";
        }
      }
      updateDialogBtns();
    });
  });

  $$("[data-douyin-subtab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      const nextSub = tab.dataset.douyinSubtab;
      dialog.douyinSubTab = nextSub;
      if (dialog.mode === "create") {
        dialog.videos = [];
        dialog.selectedVideos = new Set();
        dialog.collectionCursor = 0;
        dialog.collectionHasMore = false;
        $("#dialogCollectionMoreBtn")?.classList.add("hidden");
        if (nextSub !== "links") {
          $("#dialogCustomLinks").value = "";
        }
      }
      setDouyinSubTab(nextSub);
    });
  });

  $("#dialogDouyinAccount")?.addEventListener("change", () => {
    dialog.douyinCookieIndex = Number($("#dialogDouyinAccount").value || 0);
    dialog.collectionCursor = 0;
    dialog.collectionHasMore = false;
    $("#dialogCollectionMoreBtn")?.classList.add("hidden");
  });
  $("#dialogCollectionBtn")?.addEventListener("click", () => dialogFetchCollection(true));
  $("#dialogCollectionMoreBtn")?.addEventListener("click", () => dialogFetchCollection(false));
  $("#dialogCollectionAllBtn")?.addEventListener("click", dialogFetchCollectionAll);

  $$("[data-dialog-cat]").forEach((chip) => {
    chip.addEventListener("click", () => {
      dialog.channelFilter = chip.dataset.dialogCat;
      $$("[data-dialog-cat]").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      renderDialogChannels();
    });
  });

  $("#dialogChannelBtns").addEventListener("click", (e) => {
    const btn = e.target.closest(".toggle-btn");
    if (btn?.dataset.key) toggleDialogChannel(btn.dataset.key);
  });

  $("#dialogSelectAllCh").addEventListener("click", () => {
    state.channels
      .filter((ch) => dialog.channelFilter === "all" || ch.category === dialog.channelFilter)
      .forEach((ch) => dialog.selectedChannels.add(channelKey(ch)));
    renderDialogChannels();
    updateDialogBtns();
  });
  $("#dialogDeselectAllCh").addEventListener("click", () => {
    if (dialog.channelFilter === "all") {
      dialog.selectedChannels.clear();
    } else {
      state.channels
        .filter((ch) => ch.category === dialog.channelFilter)
        .forEach((ch) => dialog.selectedChannels.delete(channelKey(ch)));
    }
    renderDialogChannels();
    updateDialogBtns();
  });

  $("#dialogVideoList").addEventListener("click", (e) => {
    const item = e.target.closest(".video-select-item");
    if (item?.dataset.id) toggleDialogVideo(item.dataset.id);
  });

  $("#dialogSelectAllVid").addEventListener("click", () => {
    dialog.videos.forEach((v) => dialog.selectedVideos.add(v.id));
    renderDialogVideos();
    updateDialogBtns();
  });
  $("#dialogDeselectAllVid").addEventListener("click", () => {
    dialog.selectedVideos = new Set(dialog.lockedVideoIds);
    renderDialogVideos();
    updateDialogBtns();
  });

  $("#dialogSelectAllAcc").addEventListener("click", () => {
    state.accounts.forEach((a) => dialog.selectedAccounts.add(a.id));
    renderDialogAccounts();
  });
  $("#dialogDeselectQq").addEventListener("click", () => {
    state.accounts.filter((a) => a.type === "qq").forEach((a) => dialog.selectedAccounts.delete(a.id));
    renderDialogAccounts();
  });
  $("#dialogDeselectBot").addEventListener("click", () => {
    state.accounts.filter((a) => a.type === "bot").forEach((a) => dialog.selectedAccounts.delete(a.id));
    renderDialogAccounts();
  });

  $("#taskDialog").addEventListener("change", (e) => {
    const input = e.target.closest(".account-groups input[data-account]");
    if (input) setDialogAccount(input.dataset.account, input.checked);
  });

  $("#dialogCreate").addEventListener("click", () => {
    submitDialog(isDialogCustomMode() ? "custom" : "once");
  });
  $("#dialogCreateRecurring").addEventListener("click", () => submitDialog("recurring"));
  $("#dialogParseLinksBtn")?.addEventListener("click", dialogParseLinks);
  $("#appendLinksBtn")?.addEventListener("click", openAppendLinksDialog);
  $("#syncCollectionBtn")?.addEventListener("click", syncTaskCollection);
  $("#closeAppendLinksDialog")?.addEventListener("click", closeAppendLinksDialog);
  $("#appendLinksCancel")?.addEventListener("click", closeAppendLinksDialog);
  $("#appendLinksConfirm")?.addEventListener("click", submitAppendLinks);

  $("#sidebarTaskList").addEventListener("click", (e) => {
    const item = e.target.closest(".sidebar-item");
    if (item?.dataset.id) selectTask(item.dataset.id);
  });

  $("#startTaskBtn").addEventListener("click", startTask);
  $("#pauseTaskBtn").addEventListener("click", pauseTask);
  $("#deleteTaskBtn").addEventListener("click", () => deleteTask());

  $("#videoProgressPanel")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-send-video]");
    if (btn?.dataset.sendVideo) sendVideoManual(btn.dataset.sendVideo);
  });

  $("#videoProgressDoneWrap")?.addEventListener("click", (e) => {
    if (e.target.closest("#toggleDoneVideosBtn")) {
      state.doneVideosExpanded = !state.doneVideosExpanded;
      if (state.taskDetail) {
        updateVideoProgress(state.taskDetail.video_progress || [], true, state.taskDetail.status, state.taskDetail);
      }
    }
  });

  window.addEventListener("popstate", async () => {
    if (isAutoLikeRoute()) {
      await openAutoLikeView({ updateUrl: false });
      return;
    }
    if (isSettingsRoute()) {
      await openSettingsView({ updateUrl: false });
      return;
    }
    const urlId = getTaskIdFromUrl();
    if (urlId && state.tasks.some((t) => t.task_id === urlId)) {
      if (urlId !== state.selectedTaskId || state.mainView === "auto-like" || state.mainView === "settings") {
        await selectTask(urlId, { updateUrl: false });
      }
      return;
    }
    state.selectedTaskId = null;
    state.taskDetail = null;
    showEmptyState();
  });
}

async function bootstrapApp() {
  await loadMeta();
  await refreshTasks();

  if (isAutoLikeRoute()) {
    await openAutoLikeView({ updateUrl: false });
  } else if (isSettingsRoute()) {
    await openSettingsView({ updateUrl: false });
  } else {
    const urlId = getTaskIdFromUrl();
    const savedId = loadSelectedTask();
    const targetId = urlId || savedId;
    if (targetId && state.tasks.some((t) => t.task_id === targetId)) {
      await selectTask(targetId, { updateUrl: !urlId, replaceUrl: !urlId });
    } else {
      if (urlId) clearTaskUrl();
      saveSelectedTask();
      showEmptyState();
    }
  }

  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(pollDetail, 2000);
}

async function init() {
  bindEvents();
  bindCronInput();

  const ok = await ensureAuth();
  if (!ok) return;

  await bootstrapApp();
}

init();
