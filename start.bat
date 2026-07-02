@echo off
REM 腾讯频道发帖 Web 工具 — 一键启动（Windows）
setlocal

cd /d "%~dp0"

where powershell >nul 2>&1
if errorlevel 1 (
  echo 错误: 未找到 PowerShell，请安装 Windows PowerShell 5.1 或更高版本。
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
