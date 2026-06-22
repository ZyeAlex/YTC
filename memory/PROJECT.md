# 腾讯频道发帖 Web 项目 — Memory

> 供后续迭代与**服务器搬运**对照。最后更新：2026-06-19  
> 原始 CLI 工具包完整归档见 [ORIGINAL_TOOLKIT.md](ORIGINAL_TOOLKIT.md)

## 项目定位

可视化网页：搜索 B站/抖音 → **全部视频**批量发送 → 多频道、多账号。支持：

- **发帖任务**（一次性 / 长期 Cron 搜索+发帖）
- **自动点赞任务**（按频道独立配置，Cron 可选，支持手动「执行」）

---

## 当前状态（2026-06-19）

**本地功能已打通**，可作为搬运到服务器的基线：

| 模块 | 状态 | 说明 |
|------|------|------|
| B站搜索 + 下载 + 发帖 | ✅ | yt-dlp + Netscape Cookie |
| 抖音搜索 + 下载 + 发帖 | ✅ | GUAIKEI API + nocookie 解析 |
| 发帖任务调度 | ✅ | `cache/tasks.json` 持久化，重启续跑 |
| 自动点赞 | ✅ | 独立任务文件，UI `/auto-like` |
| 配置与任务分离 | ✅ | 见下文「数据分层」 |
| 多账号隔离发帖 | ✅ | `cli_env.py` 每频道随机换号 |
| Cron 分钟选择器 | ✅ | 发帖默认 `0,20,40`；**点赞默认空**（不填则不定时） |

**搬运前注意：** 无 Web 鉴权；`tencent-channel-cli` 当前 bundle 为 **darwin-arm64**，Linux 服务器需补对应二进制包。

---

## 目录结构

```
腾讯频道/
├── start.sh / run.py
├── pyproject.toml / requirements.txt
├── config/
│   ├── config.json                     # ★ 静态业务配置（敏感）
│   ├── config.json.example
│   └── bili_cookie_netscape.txt        # 可选：与 config 内 netscape 二选一/互补
├── skills/                             # 项目内 skill，不依赖全局 npm
│   ├── douyin-search-keyword/
│   └── tencent-channel-cli/            # 含平台原生二进制（需按 OS 补包）
├── backend/
│   ├── main.py                         # FastAPI + lifespan 调度
│   ├── config.py                       # 路径、CLI/ffmpeg/yt-dlp 解析
│   ├── schedule.py                     # Cron 校验 / 下一档时间
│   ├── tasks.py                        # 发帖任务调度
│   ├── auto_like_scheduler.py          # 点赞任务调度（20s 轮询）
│   ├── models.py
│   ├── data/
│   │   ├── app_config.py               # config.json 读写（带锁 + 防清空）
│   │   ├── auto_like_tasks.py          # cache/auto_like_tasks.json 读写
│   │   ├── accounts.py / channels.py / filter_patterns.py
│   ├── services/
│   │   ├── search_*.py / download.py / publish.py / video_filter.py
│   │   ├── auto_like.py                # 拉帖、过滤、点赞 CLI
│   │   └── cli_env.py                  # 子进程隔离 HOME + Token
│   └── tools/douyin_nocookie.py
├── static/
│   ├── index.html / app.js / style.css
│   └── cron-picker.js / cron-picker.css
├── memory/
├── downloads/                          # 临时视频（可清）
├── cache/
│   ├── tasks.json                      # ★ 发帖任务状态
│   ├── auto_like_tasks.json            # ★ 自动点赞任务状态
│   └── bili_cookie_netscape.txt        # yt-dlp 运行时缓存
└── .venv/
```

**不再使用：** 分散的 `config/accounts.json`、`channels.json` 等；一切静态配置只在 `config.json`。

---

## 数据分层（重要）

| 文件 | 性质 | 内容 |
|------|------|------|
| `config/config.json` | **配置** | Token、频道列表、Cookie、过滤词 |
| `cache/tasks.json` | **发帖任务** | 任务队列、进度、日志、Cron |
| `cache/auto_like_tasks.json` | **点赞任务** | 每频道 enabled、点赞范围、Cron、运行日志、`next_run_at` 等 |

**原则：** 点赞/发帖的运行态、日志、调度水位 **绝不写入** `config.json`。  
`app_config.py` 对 config 有线程锁、原子写、`.bak` 备份、拒绝误清空 channels/accounts。

首次启动若 `config.json` 仍含旧字段 `auto_like`，会自动迁移到 `cache/auto_like_tasks.json` 并剥离。

---

## 启动

```bash
./start.sh    # 默认 http://0.0.0.0:8765（所有网卡可访问）
```

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `HOST` | `0.0.0.0` | 仅本机访问可设为 `127.0.0.1` |
| `PORT` | `8765` | |
| `OPEN_BROWSER` | `1`（仅 macOS） | Linux 设为 `0` |
| `GUAIKEI_API_TOKEN` | config 内字段 | 可覆盖抖音 Token |
| `TENCENT_CHANNEL_CLI` / `_BINARY` | skills 内路径 | 可指向服务器上的 CLI |
| `YT_DLP` / `FFMPEG_PATH` | `.venv` 内 | 可选覆盖 |

---

## 依赖

```bash
uv venv .venv --python 3.11
uv pip install -r requirements.txt
```

| 组件 | 用途 |
|------|------|
| fastapi / uvicorn / pydantic | Web |
| yt-dlp | B站下载 |
| imageio-ffmpeg | ffmpeg（`.venv` 内） |
| croniter | Cron |
| node ≥16 | 抖音搜索 JS |
| skills/tencent-channel-cli | 发帖 + 点赞 CLI |

**不需：** 全局 ffmpeg、~/.qclaw、npm 全局 CLI。

---

## 统一配置 `config/config.json`

| 字段 | 用途 |
|------|------|
| `guaikei_api_token` | 抖音搜索 |
| `bili.search_cookie` | B站搜索 |
| `bili.download_cookie_netscape` | B站下载（必须，否则 412） |
| `accounts.qq_accounts` / `bot_accounts` | index + name + token |
| `channels` | name / guild_id / channel_id / category / active |
| `filter_patterns` | 标题过滤 |

账号 ID：`qq:1`~`qq:8`，`bot:1`~`bot:24`。

---

## API

### 发帖

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/channels` | 频道列表 |
| PUT | `/api/channels/order` | 排序 → config.json |
| GET | `/api/accounts` | 账号（无 token） |
| GET/PUT | `/api/filter-patterns` | 过滤词 |
| POST | `/api/search` | 搜索 |
| POST | `/api/schedule/preview` | Cron 预览 |
| GET/POST/PUT/DELETE | `/api/tasks`… | 任务 CRUD + 启停 + 单条发送 |

### 自动点赞

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings/auto-like` | 任务列表 + 运行状态 |
| PUT | `/api/settings/auto-like` | 批量保存（少用） |
| PUT | `/api/settings/auto-like/channel/{guild}/{channel}` | 单频道 upsert |
| POST | `/api/settings/auto-like/run/{guild}/{channel}` | 立即执行一轮 |
| GET | `/auto-like` | 前端路由（同 index.html） |

前端：`static/app.js`，左侧频道来自 `config.channels`，点赞配置来自 `auto_like_tasks.json`。

---

## 发帖任务

- **once**：发完队列内全部视频
- **recurring**：按关键词定时搜新视频并追加
- Cron 仅分钟段，如 `9,29,49 * * * *`
- 每条视频：**每频道随机换号**发帖；频道间隔 10~20s；20063 限流同频道换号最多 3 次
- 持久化 `cache/tasks.json`，`running` 任务重启续跑

## 自动点赞任务

- 每频道独立：likes_min/max、only_own_posts、account_ids、feeds_per_channel
- **schedule_cron 默认为空**：未配置则仅手动「执行」，调度器跳过
- 配置 Cron 后调度器每 20s 检查 `next_run_at`
- 运行中 UI 只读 + 日志；停止后可编辑
- 本系统帖子识别：feed author 字符串 + `guild-member-search` 映射 tinyid

---

## 服务器搬运清单

### 1. 拷贝内容

```bash
# 必需
项目源码（含 skills/、backend/、static/）
config/config.json          # 敏感，单独 secure copy
config/bili_cookie_netscape.txt   # 若 config 内已内嵌可不带

# 可选（保留运行态）
cache/tasks.json
cache/auto_like_tasks.json

# 不要依赖
.venv/ downloads/ cache/bili_cookie_netscape.txt（服务器上会自动生成）
```

### 2. 服务器环境

```bash
# 示例：Ubuntu/Debian
curl -LsSf https://astral.sh/uv/install.sh | sh
# node 18+（抖音需要）
# 其余由 start.sh + uv pip 安装
```

### 3. Linux 专用：tencent-channel-cli

当前 `skills/tencent-channel-cli/node_modules/` 多为 **darwin-arm64**。在目标机器：

```bash
cd skills/tencent-channel-cli
npm install tencent-channel-cli-linux-x64   # 或对应架构
# 或设置环境变量 TENCENT_CHANNEL_CLI_BINARY 指向二进制
```

`backend/config.py` 的 `find_cli_binary()` 会按 `platform` + `machine` 查找 `node_modules/tencent-channel-cli-{plat}-{arch}/bin/`。

### 4. 启动服务

```bash
export OPEN_BROWSER=0   # Linux 服务器
./start.sh              # 默认已监听 0.0.0.0:8765
```

生产建议：`systemd` 单元或 `tmux`/`screen` 守护；前面加 **防火墙 / VPN**，因无登录鉴权。

### 5. 搬运后验收

```bash
curl -s http://127.0.0.1:8765/api/channels | python3 -c "import sys,json; print(len(json.load(sys.stdin)['channels']))"
curl -s http://127.0.0.1:8765/api/accounts  | python3 -c "import sys,json; print(len(json.load(sys.stdin)['accounts']))"
curl -s http://127.0.0.1:8765/api/tasks      | python3 -c "import sys,json; print(len(json.load(sys.stdin)['tasks']))"
curl -s http://127.0.0.1:8765/api/settings/auto-like | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['config']['channels']))"
```

浏览器：主页发帖流程 + `/auto-like` 频道列表与执行/停止。

---

## 常见改动入口

| 需求 | 文件 |
|------|------|
| 静态配置 | `config/config.json` |
| 配置读写 / 防清空 | `backend/data/app_config.py` |
| 点赞任务读写 | `backend/data/auto_like_tasks.py` |
| 发帖调度 | `backend/tasks.py` |
| 点赞调度 | `backend/auto_like_scheduler.py` |
| 点赞逻辑 | `backend/services/auto_like.py` |
| Cron | `backend/schedule.py`, `static/cron-picker.js` |
| 前端 | `static/app.js`, `static/index.html` |

---

## 已知限制

- 无用户认证，勿公网裸奔
- GUAIKEI 抖音搜索有日限额
- Cookie / Token 会过期，更新 `config.json` 后重启
- macOS 开发、`start.sh` 含 QClaw PATH；Linux 可删或忽略
- `auto-like-bot 2/` 为参考原型，**不参与运行**

---

## 发帖铁律

1. 不降级：下载失败不发链接  
2. 限流（20063）才换号重试  
3. 视频 >200MB 跳过  
4. B站下载必须 Cookie  
