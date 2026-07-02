#!/usr/bin/env python3
"""启动腾讯频道发帖 Web 服务（需先运行 start.sh / start.bat）"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"

if not VENV_PYTHON.exists():
    starter = "start.bat" if sys.platform == "win32" else "start.sh"
    print(f"请先运行: ./{starter}")
    sys.exit(1)

subprocess.run([str(VENV_PYTHON), "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765"], check=True)
