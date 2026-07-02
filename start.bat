@echo off
setlocal

cd /d "%~dp0"

where powershell >nul 2>&1
if errorlevel 1 (
  echo Error: PowerShell not found. Please install PowerShell 5.1 or later.
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%
