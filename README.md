# 腾讯频道发帖 Web 工具

搜索 B站/抖音视频，批量发送到腾讯频道。

## 1. 安装环境

**后端（必需）**

克隆后直接运行启动脚本即可（**无需预装 Python**；会自动通过国内镜像安装 uv、Node.js、Python 3.11、项目依赖及 `tencent-channel-cli`）：

**macOS / Linux**

```bash
./start.sh
```

**Windows**

```bat
start.bat
```

国内镜像（已内置，无需手动配置）：
- Python：`npmmirror.com` → `ghfast.top` 备用
- PyPI：`mirrors.aliyun.com`
- Node / npm：`npmmirror.com`

## 2. 配置

首次运行会自动从 `config/config.template.json` 生成 `config/config.json`，编辑后填写：

| 字段 | 说明 |
|------|------|
| `access_token` | 登录 Token（首次打开页面可直接在登录框设置，会自动写入 config.json） |
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

## 4. Windows 便携版（免安装 exe）

若不想折腾 Python / Node / ffmpeg 环境，可在 **Windows** 上打包成绿色便携版：

```bat
build_exe.bat
```

打包完成后，产物在 `dist\腾讯频道发帖工具\`：

- 双击 **`腾讯频道发帖工具.exe`** 即可启动（会自动打开浏览器）
- 将整个文件夹打成 zip 拷贝到其他 Windows 电脑，解压后同样可用
- `config/`、`downloads/`、`cache/` 保存在 exe 同目录，升级时保留这些文件夹即可

**说明：**

- 打包必须在 Windows 上执行（PyInstaller 无法跨平台编译 exe）
- 首次打包会自动下载 Node.js 与 `tencent-channel-cli-win32-x64`，体积约 300–500 MB
- 关闭黑色命令行窗口即停止服务

## 作者

子叶Alex

## 联系方式

加频道群 1084648139，@384365260