/**
 * 抖音搜索模块
 */
const constants = require("../config/constants");
const { getJson, requestApi } = require("../utils/request");
const { AuthError } = require("../utils/errors");

/**
 * 处理搜索结果数据
 * @param {Array} data - 原始搜索结果数组
 * @returns {Array} 处理后的结果数组
 */
function processSearchResults(data) {
  if (!Array.isArray(data)) {
    return [];
  }

  return data.map((item) => {
    const processedItem = { ...item };

    if (item.author_sec_uid) {
      processedItem.author_url = `https://www.douyin.com/user/${item.author_sec_uid}`;
    }

    if (item.create_time && !item.create_time_str) {
      processedItem.create_time_str = new Date(
        item.create_time * 1000,
      ).toLocaleString();
    }

    return processedItem;
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isTaskFinished(response) {
  if (!response || typeof response !== "object") {
    return false;
  }
  if (response.finished === true || response.is_finish === 1) {
    return true;
  }
  const status = String(response.status || response.task_status || "").toLowerCase();
  return status === "done" || status === "success" || status === "finished";
}

function isFatalSearchError(err) {
  if (err instanceof AuthError || err?.name === "AuthError") {
    return true;
  }
  const code = String(err?.code || "").toUpperCase();
  if (code === "AUTH_ERROR") {
    return true;
  }
  const msg = String(err?.message || "");
  return /token|无效|权限|未配置|暂停服务/i.test(msg);
}

/**
 * 创建抖音搜索任务
 */
async function createSearchTask(token, keyword, sort, time, limit) {
  const params = {
    _: Date.now(),
    token: token,
  };

  const data = {
    keyword,
    sort_type: sort,
    publish_time: time,
    limit: limit,
  };

  return await requestApi(
    "POST",
    "/api/douyin/general-search/keyword",
    params,
    data,
    constants.CREATE_MAX_ATTEMPTS,
    "创建任务",
  );
}

/**
 * 轮询获取抖音搜索任务结果（固定间隔，避免指数退避导致长时间卡死）
 */
async function getSearchTask(token, keyword, sort, time, limit) {
  const maxPolls = constants.POLL_MAX_ATTEMPTS;
  const interval = constants.POLL_INTERVAL;
  let lastError = null;

  await sleep(interval);

  for (let poll = 0; poll < maxPolls; poll++) {
    const params = {
      _: Date.now(),
      token,
      keyword,
      sort_type: sort,
      publish_time: time,
      limit,
    };

    try {
      const response = await getJson("/api/douyin/general-search/info", params);
      const items = response.data;
      if (Array.isArray(items) && items.length > 0) {
        return processSearchResults(items);
      }
      if (isTaskFinished(response)) {
        return [];
      }
    } catch (err) {
      lastError = err;
      if (isFatalSearchError(err)) {
        throw err;
      }
    }

    if (poll < maxPolls - 1) {
      await sleep(interval);
    }
  }

  if (lastError) {
    throw lastError;
  }
  const waitedSec = Math.round((maxPolls * interval) / 1000);
  throw new Error(`搜索任务超时（已等待约 ${waitedSec} 秒），请稍后重试`);
}

module.exports = {
  createSearchTask,
  getSearchTask,
};
