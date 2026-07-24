# 腾讯频道发帖工具 — Windows 便携版打包脚本
# 用法：在项目根目录 PowerShell 执行 .\build_exe.ps1
# 产出：dist\腾讯频道发帖工具\  （整个文件夹可 zip 分发，双击 exe 即可运行）
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$NodeVersion = if ($env:NODE_VERSION) { $env:NODE_VERSION } else { "20.18.3" }
$AppName = "腾讯频道发帖工具"
$DistDir = Join-Path $Root "dist\$AppName"
$RuntimeDir = Join-Path $Root "packaging\runtime"
$RuntimeNode = Join-Path $RuntimeDir "node"
$RuntimeSkills = Join-Path $RuntimeDir "skills"

Write-Host "========================================"
Write-Host "  打包 $AppName 便携版"
Write-Host "========================================"

function Test-CommandExists($Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Enable-InsecureSsl {
    try {
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol
    } catch {}
    try {
        if (-not ([System.Management.Automation.PSTypeName]"InsecureSslCallback").Type) {
            Add-Type @"
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public static class InsecureSslCallback {
  public static void Enable() {
    ServicePointManager.ServerCertificateValidationCallback =
      delegate { return true; };
  }
}
"@
        }
        [InsecureSslCallback]::Enable()
    } catch {}
}

function Ensure-Uv {
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\uv;$env:PATH"
    if (Test-CommandExists "uv") { return }

    Write-Host "→ 未检测到 uv，正在安装..."
    Enable-InsecureSsl

    try {
        $script = (Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -UseBasicParsing).Content
        Invoke-Expression $script
        $env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\uv;$env:PATH"
        if (Test-CommandExists "uv") { return }
    } catch {
        Write-Host "  官方脚本失败: $($_.Exception.Message)"
    }

    foreach ($url in @(
        "https://ghfast.top/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
        "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    )) {
        try {
            $zip = Join-Path $env:TEMP "uv-win.zip"
            $extract = Join-Path $env:TEMP "uv-win-extract"
            $destDir = Join-Path $env:USERPROFILE ".local\bin"
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
            if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
            Expand-Archive -Path $zip -DestinationPath $extract -Force
            $uvExe = Get-ChildItem -Path $extract -Filter "uv.exe" -Recurse | Select-Object -First 1
            if (-not $uvExe) { throw "压缩包内未找到 uv.exe" }
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
            Copy-Item $uvExe.FullName (Join-Path $destDir "uv.exe") -Force
            $env:PATH = "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\uv;$env:PATH"
            if (Test-CommandExists "uv") { return }
        } catch {
            Write-Host "  下载失败: $($_.Exception.Message)"
        }
    }

    Write-Host "✗ uv 安装失败"
    exit 1
}

function Ensure-Venv {
    $activate = Join-Path $Root ".venv\Scripts\Activate.ps1"
    if (-not (Test-Path $activate)) {
        Write-Host "→ 创建 Python 虚拟环境..."
        if (-not $env:UV_INDEX_URL) { $env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/" }
        uv venv (Join-Path $Root ".venv") --python 3.11
    }
    . $activate
}

function Ensure-RuntimeNode {
    $nodeExe = Join-Path $RuntimeNode "node.exe"
    if (Test-Path $nodeExe) {
        Write-Host "✓ runtime/node 已就绪"
        return
    }

    Write-Host "→ 下载 Node.js v$NodeVersion (win-x64)..."
    $archive = "node-v$NodeVersion-win-x64"
    $zipName = "$archive.zip"
    $url = "https://npmmirror.com/mirrors/node/v$NodeVersion/$zipName"
    $tmpZip = Join-Path $env:TEMP $zipName

    Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing
    if (Test-Path $RuntimeNode) { Remove-Item $RuntimeNode -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    Expand-Archive -Path $tmpZip -DestinationPath $RuntimeDir -Force
    Rename-Item (Join-Path $RuntimeDir $archive) $RuntimeNode
    Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
    Write-Host "✓ Node.js 已下载到 packaging/runtime/node"
}

function Ensure-RuntimeSkills {
    $cliBin = Join-Path $RuntimeSkills "tencent-channel-cli\node_modules\tencent-channel-cli-win32-x64\bin\tencent-channel-cli.exe"
    if (Test-Path $cliBin) {
        Write-Host "✓ runtime/skills 已就绪"
        return
    }

    Write-Host "→ 准备 runtime/skills..."
    $srcSkills = Join-Path $Root "skills"
    if (Test-Path $RuntimeSkills) { Remove-Item $RuntimeSkills -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $RuntimeSkills | Out-Null

    Copy-Item (Join-Path $srcSkills "tencent-channel-cli") (Join-Path $RuntimeSkills "tencent-channel-cli") -Recurse -Force
    Copy-Item (Join-Path $srcSkills "douyin-search-keyword") (Join-Path $RuntimeSkills "douyin-search-keyword") -Recurse -Force

    $tcliDir = Join-Path $RuntimeSkills "tencent-channel-cli"
    if (Test-Path (Join-Path $tcliDir "node_modules")) {
        Remove-Item (Join-Path $tcliDir "node_modules") -Recurse -Force
    }

    if (-not $env:NPM_CONFIG_REGISTRY) { $env:NPM_CONFIG_REGISTRY = "https://registry.npmmirror.com" }
    $nodeDir = Join-Path $RuntimeNode ""
    $env:PATH = "$nodeDir;$env:PATH"

    Push-Location $tcliDir
    try {
        npm install tencent-channel-cli-win32-x64@1.0.7 --no-fund --no-audit -q
        if (-not (Test-Path $cliBin)) {
            Write-Host "✗ tencent-channel-cli-win32-x64 安装失败"
            exit 1
        }
    } finally {
        Pop-Location
    }
    Write-Host "✓ skills 已准备完成"
}

Ensure-Uv
Ensure-Venv

Write-Host "→ 安装 Python 依赖..."
if (-not $env:UV_INDEX_URL) { $env:UV_INDEX_URL = "https://mirrors.aliyun.com/pypi/simple/" }
uv pip install -q -r requirements.txt -r packaging/requirements-build.txt

Ensure-RuntimeNode
Ensure-RuntimeSkills

Write-Host "→ PyInstaller 打包中（约 2-5 分钟）..."
pyinstaller packaging/channel-poster.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ PyInstaller 打包失败"
    exit 1
}

Write-Host "→ 复制 runtime 到发布目录..."
$runtimeDst = Join-Path $DistDir "runtime"
if (Test-Path $runtimeDst) { Remove-Item $runtimeDst -Recurse -Force }
Copy-Item $RuntimeDir $runtimeDst -Recurse -Force

Write-Host ""
Write-Host "========================================"
Write-Host "✓ 打包完成"
Write-Host "  目录: $DistDir"
Write-Host "  运行: 双击 $AppName.exe"
Write-Host ""
Write-Host "  可将整个「$AppName」文件夹压缩成 zip 分发"
Write-Host "  config / downloads / cache 会保存在 exe 同目录"
Write-Host "========================================"
