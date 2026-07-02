# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 在 Windows 上执行 build_exe.ps1 自动调用。"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

block_cipher = None

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "config" / "config.template.json"), "config"),
    (str(ROOT / "backend" / "tools"), "backend/tools"),
]
datas += collect_data_files("imageio_ffmpeg", include_py_files=False)
datas += collect_data_files("yt_dlp", include_py_files=True)

hiddenimports = collect_submodules("uvicorn")
hiddenimports += collect_submodules("backend")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "croniter",
    "curl_cffi",
    "imageio_ffmpeg",
    "yt_dlp",
    "engineio.async_drivers.asgi",
]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="腾讯频道发帖工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="腾讯频道发帖工具",
)
