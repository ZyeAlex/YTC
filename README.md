# 腾讯频道发帖 Web 工具

搜索 B站/抖音视频，批量发送到腾讯频道。

## 1. 安装环境

**后端（必需）**

克隆后直接运行启动脚本即可（会自动安装 uv、Node.js、创建 `.venv`、安装 Python 依赖及 `tencent-channel-cli` 平台二进制）：

**macOS / Linux**

```bash
./start.sh
```

**Windows**

```bat
start.bat
```

## 2. 配置

首次运行会自动从 `config/config.template.json` 生成 `config/config.json`，编辑后填写：

| 字段 | 说明 |
|------|------|
| `access_token` | 登录 Token|
| `guaikei_api_token` | 抖音搜索guaikei Token（需要自动搜索时配置） |
| `bili.cookies` | B站 Cookie 列表（设置页可配，搜索默认第一条） |
| `douyin.cookies` | 抖音 Cookie 列表（设置页可配） |

## 3. 启动

**macOS / Linux**

```bash
./start.sh
```

**Windows**

```bat
start.bat
```

或在 PowerShell 中：

```powershell
.\start.ps1
```

浏览器打开 http://127.0.0.1:8765（或服务器地址）

## 作者

子叶Alex

## 联系方式

加频道群 1084648139，@384365260