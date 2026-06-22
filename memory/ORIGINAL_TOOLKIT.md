# OpenClaw Social Poster 原始工具包 — 完整归档

> 从 `docs/` 提炼，供删除 docs 后对照。最后更新：2026-06-19  
> **Web 版当前配置已统一为 `config/config.json`，skill 已迁入项目 `skills/`。**

原项目名：**OpenClaw Social Poster**，CLI + Crontab 自动发帖工具包（抖音 + B站），支持 19 个频道。

**Web 版已接管发帖功能**，本文档保留 CLI 时代的命令、规则、脚本逻辑，便于回溯或复用。

---

## 一、原目录结构

```
openclaw_social_poster/
├── README.md / INSTALL.md / CHANNELS.md / QUICKSTART.md
├── crontab.example
├── tools/
│   ├── douyin/
│   │   ├── auto_post_douyin.py    # 抖音主入口
│   │   └── douyin_nocookie.py     # 无 Cookie 解析下载
│   ├── bili/
│   │   ├── bili_auto_post.py      # B站通用入口
│   │   ├── lanxing_bili.py        # 蓝色星原专用
│   │   ├── fsea_bili.py           # 遗忘之海专用
│   │   ├── game_bili_mix.py       # 12 频道混发
│   │   ├── bili_search.py         # 搜索
│   │   ├── bili_download.py       # 下载
│   │   └── bili_search_template.py
│   └── common/
│       ├── switch_qq_account.sh   # 32 账号切换
│       ├── cron_wrapper.sh        # cron 环境包装
│       ├── tokens.json            # 账号 token（已迁到 config/accounts.json）
│       ├── auto_like.py           # 自动点赞
│       └── like_specific_feed.py  # 指定帖点赞
├── cache/                         # 视频缓存 JSON
└── logs/cron/                     # 定时任务日志
```

---

## 二、环境安装（INSTALL.md）

### macOS 必装（CLI 时代；Web 版见 PROJECT.md）

```bash
brew install node          # 抖音搜索仍需要 node
curl -LsSf https://astral.sh/uv/install.sh | sh
# Web 版：uv pip install -r requirements.txt（含 yt-dlp、imageio-ffmpeg）
# skill 已在项目 skills/ 内，无需 npm install -g
```

### PATH（cron / CLI 时代）

Web 版由 `backend/config.py` 的 `PATH_ENV` 管理，指向 `.venv/bin` 与 `skills/tencent-channel-cli/bin`。

### 可选 / 已迁移

- 抖音搜索：CLI 时代 `~/.qclaw/skills/douyin-search-keyword` → 现 **`skills/douyin-search-keyword`**
- B站 Cookie：CLI 时代独立文件 → 现 **`config/config.json`** 内 `bili.*`
- 发帖 CLI：CLI 时代全局 npm → 现 **`skills/tencent-channel-cli`**

### 验证命令

```bash
which yt-dlp ffmpeg node uv tencent-channel-cli
bash tools/common/switch_qq_account.sh
tencent-channel-cli --help
uv run python tools/bili/bili_search.py "原神" --page-size 5
```

### 故障排查

| 问题 | 解决 |
|------|------|
| 找不到 tencent-channel-cli | 用 QClaw 绝对路径 |
| yt-dlp 格式不可用 | 用 `uv run yt-dlp` 或 `.venv/bin/yt-dlp` |
| ffmpeg 找不到 | `brew install ffmpeg` |
| Python 读不到 Keychain | 必须通过 `switch_qq_account.sh` shell 切换 |

---

## 三、频道列表（CHANNELS.md）

共 19 个频道，**18 个有效**（闪耀吧噜咪已失效至 2026-06-22）。

数据已迁入 **`config/config.json`** 的 `channels` 数组，字段：`name`, `guild_id`, `channel_id`, `category`, `active`。

### 游戏频道（12+1）

| 频道 | guild_id | channel_id | 状态 |
|------|----------|------------|------|
| 蓝色星原旅谣 | 23396421665492266 | 634579922 | ✅ |
| 虚环游戏频道 | 619947944048388704 | 691657018 | ✅ |
| 绿梦时空之声 | 51300081639287433 | 637999056 | ✅ |
| 无限大 | 606133224033893040 | 669341452 | ✅ |
| 白银之城交流频道 | 609591914032752911 | 668027104 | ✅ |
| 望月社区频道 | 641025834029097806 | 661324771 | ✅ |
| 苍蓝避风港频道 | 653868994048214289 | 691221807 | ✅ |
| 遗忘之海频道 | 24411151781080644 | 733864046 | ✅ |
| 伊莫频道 | 663597204049855581 | 695916707 | ✅ |
| 闪耀吧噜咪 | 618008144049618426 | 695224347 | ❌ 失效 |
| 追逐卡蕾多游戏频道 | 651534434079858706 | 729795594 | ✅ |
| 芙娅之魂 | 652428184084624026 | 733973790 | ✅ |
| 异环频道 | 667187614031439173 | 666410629 | ✅ |

### 交友频道（7）

| 频道 | guild_id | channel_id |
|------|----------|------------|
| Cosplay二次元动漫漫展交流社 | 628559594045625106 | 686754782 |
| CPDD扩列交友处对象频道 | 656798574046898068 | 688980662 |
| 临时频道（夜幕之下） | 597668974048053190 | 690987110 |
| 临时频道（遗忘之海游戏） | 606873914050969757 | 698136899 |
| Cosplay二次元穿搭扩列聊天社群 | 587208923982022886 | 638218667 |
| Cosplay二次元交流聊天扩列社群 | 617051664021367578 | 653525703 |
| 扩列聊天交流社区 | 626473834032927708 | 668240797 |

### 查询子频道

```bash
bash tools/common/switch_qq_account.sh
tencent-channel-cli manage get-guild-channel-list --guild-id <guild_id> --json
# channel_name="全部" 为默认广场频道
```

---

## 四、账号体系（ACCOUNT_CONFIG.md）

- **8 QQ 主号**：栋华、鹤轩、鹤轩-梦、鹤轩-灵灵、printf-???、有鱼-神里流、露米、芈月
- **24 Bot 号**：Bot-1 ~ Bot-24
- 配置已迁入 **`config/accounts.json`**

### CLI 切换方式（switch_qq_account.sh）

```bash
bash tools/common/switch_qq_account.sh       # 全部随机
bash tools/common/switch_qq_account.sh qq    # 仅 QQ
bash tools/common/switch_qq_account.sh bot   # 仅 Bot
bash tools/common/switch_qq_account.sh 5     # 指定第 N 个（1-32）
```

原理：写入 macOS Keychain `qq-cli/token`，CLI 从 Keychain 读。**不要靠 `~/.qqcli/.env`**。

### Web 版差异

Web 用环境变量 `QQ_AI_CONNECT_TOKEN` 直接传 token，不走 Keychain。

### 限流

- 错误码 **20063**：换号 + 冷却 15-30 分钟
- 每频道最多 10 轮重试
- 除限流外不要手动重试

---

## 五、发帖铁律（README 必须遵守）

1. **不降级**：下载/发送失败则取消，不发链接替代
2. **唯一重试**：20063 限流换号（最多 10 轮，15-30 分钟冷却）
3. **禁止并发**：发帖前 `pgrep` 确认无同名任务
4. **频道间间隔**：CLI 默认 30-60s 随机（Web 现为 10-20s）
5. **视频上限**：>200MB 跳过
6. **短帖规则**：≤1000 字 `--content`；>1000 字 `--markdown-content` + `--title`
7. **抖音 API**：一天约 20 次搜索，缓存用完才补搜
8. **B站 Cookie**：必须 Netscape 格式
9. **yt-dlp**：优先 `.venv` 或 `uv run` 最新版
10. **CLI**：用完整路径，不用短名

---

## 六、抖音工具（auto_post_douyin.py）

### 流程

搜索 → 归一化去重 → 写缓存 → 选一条 → 下载 → 多频道发帖

### 参数

| 参数 | 说明 |
|------|------|
| `--keywords` | JSON: `[{"q":"cos","sort":0,"limit":10}]` |
| `--guild-id` / `--channel-id` | 单频道 |
| `--guilds` | 多频道 `gid:cid gid:cid` |
| `--task-name` | 缓存文件名 |
| `--no-refill` | 缓存用完退出并移除 crontab |

### sort 含义

- 0 = 综合
- 1 = 最多点赞
- 2 = 最新发布

### 搜索实现

调用 `~/.qclaw/skills/douyin-search-keyword/src/douyin/search-cli.js`，需 `GUAIKEI_API_TOKEN`。

### 下载策略

1. 优先 `play_addr` 直链 curl
2. 失败则用 `douyin_nocookie.py` 解析 `nwm_url`

### 缓存

`cache/<task-name>_cache.json`，视频状态：`pending` / `posted` / `download_failed` / `post_failed` / `too_large` / `no_video`

### 标题黑名单

代肝、代练、广告、直播回放、录播、切片等

### douyin_nocookie.py 原理

1. 短链 → video_id
2. 访问 `iesdouyin.com/share/video/{id}/`
3. 解析 `window._ROUTER_DATA` JSON
4. `play_addr.url_list[0]` 中 `playwm` → `play` 得无水印 URL
5. 图文帖（aweme_type=2 / .mp3）返回 skip

**已迁入**：`backend/tools/douyin_nocookie.py`

---

## 七、B站工具

### bili_auto_post.py

`--init` 搜100条 | `--cron` 定时发 | `--manual` 手动一条

配置项：`SEARCH_KEYWORDS`, `TARGET_GUILD_ID`, `TARGET_CHANNEL_ID`

### 单频道脚本模板（lanxing_bili / fsea_bili）

```python
CACHE_FILE = "cache/xxx_bili.json"
CRON_TAG = "xxx_bili"
GUILD_ID = "..."
CHANNEL_ID = "..."
KEYWORD = "游戏名"
RELATED_TERMS = [...]
EXCLUDE_TERMS = ["直播", "录播", "代肝", ...]
```

### game_bili_mix.py

12 个游戏频道轮询，每个发对应游戏视频，cron 每 15 分钟。

### bili_search.py

```bash
uv run python tools/bili/bili_search.py "关键词" --order pubdate --json
# order: pubdate | click | dm | stow
```

### bili_download.py

yt-dlp 下载，格式策略：1080P → 720P → 480P → best

### Cookie

Netscape 格式，扩展「Get cookies.txt LOCALLY」导出。

---

## 八、通用工具

### cron_wrapper.sh

```bash
bash tools/common/cron_wrapper.sh bili|douyin|like <command>
```

- 设置 PATH
- bili 检查 yt-dlp，douyin 检查 ffmpeg
- 防止 cron 环境缺工具

### auto_like.py

```bash
uv run python tools/common/auto_like.py \
  --guilds gid:cid:min-max gid:cid:min-max \
  --likes-per-post "3-10" \
  --feeds-per-channel 10 \
  --period-minutes 25
```

### like_specific_feed.py

```bash
uv run python tools/common/like_specific_feed.py \
  --feed-id B_xxx --guild-id xxx --channel-id xxx --count 30
```

---

## 九、Crontab 模板（crontab.example）

| 任务 | 频率 | 脚本 |
|------|------|------|
| 蓝色星原 B站 | */20 | lanxing_bili.py --cron |
| 遗忘之海 B站 | */20 | fsea_bili.py --cron |
| 12频道混发 | */15 | game_bili_mix.py --cron |
| cos/jk 抖音多频道 | 每小时 :05 | auto_post_douyin.py |
| 蓝星+碧蓝抖音 | */45 | auto_post_douyin.py 单频道 |
| 自动点赞 | :15,:40 | auto_like.py |

头部必须设 `SHELL`, `PATH`, `HOME`, `WORKSPACE`。

---

## 十、CLI 发帖命令参考

```bash
tencent-channel-cli feed publish-feed \
  --guild-id <gid> \
  --channel-id <cid> \
  --content "标题" \
  --video /path/to/video.mp4 \
  --json --yes
```

错误码：
- **20063** — 限流，换号
- **153** — OIDB 全局限流
- **10023** — 无权限

---

## 十一、Web 版与 CLI 对照

| CLI 能力 | Web 实现 |
|----------|----------|
| bili_search.py | `backend/services/search_bili.py` |
| 抖音 search-cli.js | `skills/douyin-search-keyword` + `search_douyin.py` |
| douyin_nocookie.py | `backend/tools/douyin_nocookie.py` |
| bili_download / yt-dlp | `backend/services/download.py`（+ imageio-ffmpeg） |
| auto_post 发帖逻辑 | `publish.py` + `tasks.py` |
| switch_qq_account.sh | `QQ_AI_CONNECT_TOKEN` 隔离子进程 |
| CHANNELS.md | `config/config.json` → `channels` |
| tokens.json | `config/config.json` → `accounts` |
| bili cookie | `config/config.json` → `bili` |
| cron 定时 | `schedule_cron` + `cron-picker` + `tasks.py` |
| auto_like | **未实现**（仅 CLI 有） |

---

## 十二、删除 docs 前检查清单

- [x] 频道 / 账号 / Cookie / 过滤词 → `config/config.json`
- [x] douyin_nocookie → `backend/tools/douyin_nocookie.py`
- [x] skills → `skills/douyin-search-keyword`、`skills/tencent-channel-cli`
- [x] 安装/规则/命令 → 本文件 + `memory/PROJECT.md`
- [ ] 若仍需 CLI 定时任务或点赞，需自行保留对应脚本

**可以安全删除 `docs/`**，Web 发帖不再依赖它。
