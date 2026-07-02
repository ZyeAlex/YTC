# 腾讯频道发帖 Web 工具 — 一键启动（Windows PowerShell）
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$HostAddr = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$Port = if ($env:PORT) { [int]$env:PORT } else { 8765 }

$env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\uv;$env:PATH"

Write-Host "========================================"
Write-Host "  腾讯频道发帖工具"
Write-Host "========================================"

if (-not (Test-Path "$Root\.venv")) {
    Write-Host "→ 创建 uv 虚拟环境..."
    try {
        & uv venv "$Root\.venv" --python 3.11 2>$null
    } catch {
        & uv venv "$Root\.venv"
    }
}

$ActivateScript = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (Test-Path $ActivateScript) {
    . $ActivateScript
}

& uv pip install -q -r requirements.txt 2>$null

function Test-CommandExists($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

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

try {
    $ffmpegPath = python -c "from backend.config import FFMPEG_PATH; print(FFMPEG_PATH or '')" 2>$null
    if ($ffmpegPath) {
        Write-Host "✓ ffmpeg $ffmpegPath"
    } else {
        Write-Host "⚠ ffmpeg 未找到（请 uv pip install imageio-ffmpeg）"
    }
} catch {
    Write-Host "⚠ ffmpeg 未找到（请 uv pip install imageio-ffmpeg）"
}

$CliWrapper = Join-Path $Root "skills\tencent-channel-cli\bin\tencent-channel-cli"
if (Test-Path $CliWrapper) {
    Write-Host "✓ tencent-channel-cli (skills/)"
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
