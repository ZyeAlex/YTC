#!/usr/bin/env python3
"""启动腾讯频道发帖 Web 服务（需先运行 ./setup.sh）"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"

if not VENV_PYTHON.exists():
    print("请先运行: ./setup.sh")
    sys.exit(1)

subprocess.run([str(VENV_PYTHON), "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765"], check=True)
