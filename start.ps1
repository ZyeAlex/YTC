# 腾讯频道发帖 Web 工具 — 一键启动（Windows PowerShell）
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$HostAddr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$Port = if ($env:PORT) { [int]$env:PORT } else { 8765 }

$env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\uv;$env:PATH"

$Script:PythonMirrors = @(
    "https://registry.npmmirror.com/-/binary/python-build-standalone",
    "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"
)

function Set-ChinaMirrors {
    if (-not $env:UV_PYTHON_PREFERENCE) { $env:UV_PYTHON_PREFERENCE = "only-managed" }
    if (-not $env:UV_INDEX_URL) { $env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/" }
    if (-not $env:NPM_CONFIG_REGISTRY) { $env:NPM_CONFIG_REGISTRY = "https://registry.npmmirror.com" }
}

Write-Host "========================================"
Write-Host "  腾讯频道发帖工具"
Write-Host "========================================"

function Test-CommandExists($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Uv {
    if (Test-CommandExists "uv") { return }
    Write-Host "→ 未检测到 uv，正在安装..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\uv;$env:PATH"
    if (-not (Test-CommandExists "uv")) {
        Write-Host "✗ uv 安装失败，请手动安装: https://docs.astral.sh/uv/"
        exit 1
    }
}

function Ensure-Node {
    $NodeVersion = if ($env:NODE_VERSION) { $env:NODE_VERSION } else { "20.18.3" }
    $ToolsNode = Join-Path $Root ".tools\node"
    $nodeExe = Join-Path $ToolsNode "node.exe"

    if (Test-Path $nodeExe) {
        $env:PATH = "$ToolsNode;$env:PATH"
    }
    if ((Test-CommandExists "node") -and (Test-CommandExists "npm")) { return }

    Write-Host "→ 未检测到 Node.js，正在安装到 .tools\node (v$NodeVersion)..."

    $archive = "node-v$NodeVersion-win-x64"
    $zipName = "$archive.zip"
    $url = "https://npmmirror.com/mirrors/node/v$NodeVersion/$zipName"
    $tmpZip = Join-Path $env:TEMP $zipName
    $toolsDir = Join-Path $Root ".tools"

    Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing
    if (Test-Path $ToolsNode) { Remove-Item $ToolsNode -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    Expand-Archive -Path $tmpZip -DestinationPath $toolsDir -Force
    Rename-Item (Join-Path $toolsDir $archive) $ToolsNode
    Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue

    $env:PATH = "$ToolsNode;$env:PATH"
    if (-not (Test-CommandExists "node") -or -not (Test-CommandExists "npm")) {
        Write-Host "✗ Node.js 安装失败"
        exit 1
    }
    Write-Host "✓ node $(node --version 2>$null)"
}

function Invoke-NativeQuiet {
    param([scriptblock]$Command)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Command
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
}

function Test-VenvReady {
    return Test-Path (Join-Path $Root ".venv\Scripts\Activate.ps1")
}

function Ensure-Venv {
    if (Test-VenvReady) { return }

    Write-Host "→ 创建 Python 虚拟环境（国内镜像）..."
    $venvPath = Join-Path $Root ".venv"

    foreach ($mirror in $Script:PythonMirrors) {
        $env:UV_PYTHON_INSTALL_MIRROR = $mirror
        Write-Host "  镜像: $mirror"
        foreach ($pyVer in @("3.11", "3.12")) {
            Write-Host "  下载 Python $pyVer ..."
            Invoke-NativeQuiet { uv venv $venvPath --python $pyVer } | Out-Null
            if (Test-VenvReady) {
                Write-Host "✓ Python 虚拟环境已创建"
                return
            }
        }
    }

    Write-Host "✗ 无法创建虚拟环境（已尝试国内镜像）"
    exit 1
}

function Ensure-PythonDeps {
    if (-not (Test-VenvReady)) {
        Write-Host "✗ 虚拟环境不存在，无法安装 Python 依赖"
        exit 1
    }

    Write-Host "→ 安装 Python 依赖（国内 PyPI 镜像）..."
    Invoke-NativeQuiet { uv pip install -q -r requirements.txt } | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✗ Python 依赖安装失败"
        exit 1
    }
    try {
        python -c "from backend.config import FFMPEG_CLI_PATH, FFMPEG_PATH; import sys; sys.exit(0 if (FFMPEG_CLI_PATH or FFMPEG_PATH) else 1)" 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "⚠ imageio-ffmpeg 未就绪，视频发帖可能失败"
        }
    } catch {
        Write-Host "⚠ imageio-ffmpeg 未就绪，视频发帖可能失败"
    }
}

function Ensure-TencentCli {
    $dir = Join-Path $Root "skills\tencent-channel-cli"
    if (-not (Test-Path $dir)) { return }
    if (-not (Test-CommandExists "node")) { return }
    if (-not (Test-CommandExists "npm")) { return }

    $pkg = "tencent-channel-cli-win32-x64"
    $bin = Join-Path $dir "node_modules\$pkg\bin\tencent-channel-cli.exe"
    if (Test-Path $bin) { return }

    Write-Host "→ 安装 tencent-channel-cli ($pkg)..."
    Push-Location $dir
    try {
        Invoke-NativeQuiet { npm install "${pkg}@1.0.7" --no-fund --no-audit -q } | Out-Null
        if (-not (Test-Path $bin)) {
            Invoke-NativeQuiet { npm install --no-fund --no-audit --omit=dev -q } | Out-Null
        }
        if (-not (Test-Path $bin)) {
            Write-Host "⚠ 未找到 $pkg 二进制，请手动在 skills/tencent-channel-cli 执行: npm install $pkg"
        }
    } finally {
        Pop-Location
    }
}

Ensure-Uv
Set-ChinaMirrors
Ensure-Node
Ensure-Venv

$ActivateScript = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    . $ActivateScript
}

Ensure-PythonDeps
Ensure-TencentCli

Write-Host "→ 检查工具..."

$YtDlpBin = Join-Path $Root ".venv\Scripts\yt-dlp.exe"
if (Test-Path $YtDlpBin) {
    $ver = & $YtDlpBin --version 2>$null | Select-Object -First 1
    Write-Host "✓ yt-dlp $ver"
} elseif (Test-CommandExists "yt-dlp") {
    Write-Host "✓ yt-dlp"
} else {
    Write-Host "⚠ yt-dlp 未安装"
}

$FfmpegShim = Join-Path $Root ".venv\Scripts\ffmpeg.exe"
if (Test-Path $FfmpegShim) {
    Write-Host "✓ ffmpeg $FfmpegShim"
} else {
    try {
        $ffmpegPath = python -c "from backend.config import FFMPEG_PATH; print(FFMPEG_PATH or '')" 2>$null
        if ($ffmpegPath) {
            Write-Host "⚠ ffmpeg 已安装但未生成 .venv\Scripts\ffmpeg.exe，视频发帖可能失败"
            Write-Host "  源文件: $ffmpegPath"
        } else {
            Write-Host "⚠ ffmpeg 未找到（请 uv pip install imageio-ffmpeg）"
        }
    } catch {
        Write-Host "⚠ ffmpeg 未找到（请 uv pip install imageio-ffmpeg）"
    }
}

$CliBin = Join-Path $Root "skills\tencent-channel-cli\node_modules\tencent-channel-cli-win32-x64\bin\tencent-channel-cli.exe"
if (Test-Path $CliBin) {
    Write-Host "✓ tencent-channel-cli (win32-x64)"
} elseif (Test-Path (Join-Path $Root "skills\tencent-channel-cli\bin\tencent-channel-cli")) {
    Write-Host "⚠ tencent-channel-cli wrapper 存在，但 win32-x64 二进制未安装"
} else {
    Write-Host "⚠ skills/tencent-channel-cli 未找到"
}

if (Test-CommandExists "node") {
    $nodeVer = node --version 2>$null
    Write-Host "✓ node $nodeVer"
} else {
    Write-Host "⚠ node 未安装（抖音搜索需要）"
}

$ConfigPath = Join-Path $Root "config\config.json"
$TemplatePath = Join-Path $Root "config\config.template.json"
if (-not (Test-Path $ConfigPath)) {
    if (Test-Path $TemplatePath) {
        Copy-Item $TemplatePath $ConfigPath
        Write-Host "✓ 已从 config.template.json 创建 config/config.json，请填写 Token 与 Cookie"
    } else {
        Write-Host "⚠ 未找到 config/config.template.json，无法自动创建 config.json"
    }
}

function Stop-PortListeners($PortNum) {
    $pids = @()
    try {
        $pids = @(Get-NetTCPConnection -LocalPort $PortNum -State Listen -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $pids = @(netstat -ano |
            Select-String ":$PortNum\s" |
            Select-String "LISTENING" |
            ForEach-Object {
                $parts = ($_ -replace '\s+', ' ').ToString().Trim().Split(' ')
                [int]$parts[-1]
            } |
            Select-Object -Unique)
    }
    if ($pids.Count -gt 0) {
        Write-Host "→ 端口 $PortNum 已被占用，正在释放..."
        foreach ($procId in $pids) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
}

Stop-PortListeners $Port

New-Item -ItemType Directory -Force -Path (Join-Path $Root "downloads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "cache") | Out-Null

if ($HostAddr -eq "0.0.0.0" -or $HostAddr -eq "::") {
    $LanIp = $null
    try {
        $LanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
            Select-Object -First 1).IPAddress
    } catch {
        $match = ipconfig | Select-String "IPv4" | Select-Object -First 1
        if ($match) {
            $LanIp = ($match -replace '.*:\s*', '').Trim()
        }
    }
    $Url = "http://127.0.0.1:${Port}"
    if ($LanIp) {
        $Url = "$Url  （局域网: http://${LanIp}:${Port}）"
    } else {
        $Url = "$Url  （已监听所有网卡，可用本机 IP 访问）"
    }
} else {
    $Url = "http://${HostAddr}:${Port}"
}

Write-Host ""
Write-Host "🚀 启动服务: $Url"
Write-Host "   按 Ctrl+C 停止"
Write-Host ""

if (-not $env:OPEN_BROWSER) { $env:OPEN_BROWSER = "1" }
if ($env:OPEN_BROWSER -eq "1") {
    Start-Job -ScriptBlock {
        param($P)
        Start-Sleep -Seconds 1.5
        Start-Process "http://127.0.0.1:$P"
    } -ArgumentList $Port | Out-Null
}

python -m uvicorn backend.main:app --host $HostAddr --port $Port
