#!/usr/bin/env python3
"""Start trading-lab UI on port 8502 (delegates to lab copy)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LAB_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_SCRIPTS.parent))

from lab.config import LAB_PORT, resolve_paths  # noqa: E402
from platform_utils import venv_python  # noqa: E402


def main() -> int:
    paths = resolve_paths()
    lab_runner = paths.lab_root / "scripts" / "lab" / "_lab_streamlit.py"
    if not lab_runner.exists():
        print("Lab copy missing. Run first:")
        print("  python scripts/lab/create_backup.py")
        return 1

    lab_py = paths.lab_backend / ".venv" / "bin" / "python"
    py = str(lab_py if lab_py.exists() else venv_python())
    print(f"Starting lab at http://localhost:{LAB_PORT}")
    return subprocess.call([py, str(lab_runner)], cwd=paths.lab_root)


if __name__ == "__main__":
    raise SystemExit(main())
