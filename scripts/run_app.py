"""Run startup checklist, then launch Streamlit UI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT / "scripts"))

from platform_utils import IS_WINDOWS, print_header, venv_python  # noqa: E402
from startup_checklist import run_startup_checklist  # noqa: E402


def main() -> int:
    os.environ.setdefault("TRADING_UI_PORT", "8501")
    os.environ.pop("LAB_MODE", None)

    code = run_startup_checklist(strict=True, run_migrate=True)
    if code != 0:
        print()
        print("Startup checks failed. Fix issues above or run: python Setup.py")
        return code

    print_header("Starting Streamlit UI")
    py = str(venv_python())
    cmd = [
        py,
        "-m",
        "streamlit",
        "run",
        "ui/dashboard.py",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
    ]
    print(f"  Command: {' '.join(cmd)}")
    print("  Open http://localhost:8501")
    print()
    try:
        return subprocess.call(cmd, cwd=BACKEND)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
