#!/usr/bin/env python3
"""Start the trading-lab background job worker (port 8502 companion process)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LAB_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_SCRIPTS.parent))

from lab.config import resolve_paths  # noqa: E402
from platform_utils import venv_python  # noqa: E402


def main() -> int:
    paths = resolve_paths()
    if not paths.lab_root.is_dir():
        print("Lab copy missing. Run: python scripts/lab/create_backup.py")
        return 1

    lab_py = paths.lab_backend / ".venv" / "bin" / "python"
    py = str(lab_py if lab_py.exists() else venv_python())
    backend = paths.lab_backend
    print("Starting lab background worker (uses trading_lab schema)")
    print(f"  cwd: {backend}")
    print("  Stop with Ctrl+C")
    return subprocess.call(
        [py, "-m", "app.jobs.worker", "--interval", "2"],
        cwd=backend,
    )


if __name__ == "__main__":
    raise SystemExit(main())
