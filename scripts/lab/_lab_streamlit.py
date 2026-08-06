#!/usr/bin/env python3
"""Launch lab Streamlit — must run with cwd = trading-lab root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab.config import LAB_PORT  # noqa: E402
from platform_utils import ROOT, venv_python  # noqa: E402
from startup_checklist import run_startup_checklist  # noqa: E402


def main() -> int:
    import os

    os.environ.setdefault("TRADING_UI_PORT", str(LAB_PORT))
    backend = ROOT / "backend"
    if not (backend / ".env").exists():
        print(f"Missing {backend / '.env'}")
        return 1

    lab_checklist = ROOT / "requirements-start-lab.txt"
    print("=" * 60)
    print("Trading LAB — startup check")
    print("=" * 60)
    code = run_startup_checklist(
        strict=True,
        run_migrate=True,
        checklist=lab_checklist if lab_checklist.exists() else None,
    )
    if code != 0:
        return code

    py = str(venv_python())
    cmd = [
        py,
        "-m",
        "streamlit",
        "run",
        "ui/dashboard.py",
        "--server.port",
        str(LAB_PORT),
        "--server.headless",
        "true",
    ]
    print()
    print(f"Lab UI: http://localhost:{LAB_PORT}")
    print()
    try:
        return subprocess.call(cmd, cwd=backend)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
