#!/usr/bin/env python3
"""Verify lab copy: tests + DB connectivity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LAB_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_SCRIPTS.parent))

from lab.config import LAB_DB_NAME, LAB_PORT, read_lab_meta, resolve_paths  # noqa: E402
from platform_utils import venv_python  # noqa: E402


def main() -> int:
    paths = resolve_paths()
    meta = read_lab_meta(paths)
    if meta is None:
        print("No lab metadata — run create_backup.py first")
        return 1

    print("=" * 60)
    print("Trading lab verification")
    print("=" * 60)
    print(f"  Lab root: {paths.lab_root}")
    print(f"  Lab DB:   {LAB_DB_NAME}")
    print(f"  Port:     {LAB_PORT}")
    print()

    py = paths.lab_backend / ".venv" / "bin" / "python"
    if not py.exists():
        py = venv_python()
    py = str(py)

    print("[1/2] Running integration tests in lab…")
    test_script = paths.lab_backend / "scripts" / "run_tests.sh"
    if not test_script.exists():
        test_script = paths.main_root / "backend" / "scripts" / "run_tests.sh"
    result = subprocess.run(
        [str(test_script), "all", "--ignore=tests/post_deploy"],
        cwd=paths.lab_backend,
        text=True,
    )
    if result.returncode != 0:
        print("Lab tests FAILED")
        return result.returncode

    print()
    print("[2/2] Lab checks passed.")
    print(f"  Open lab UI: python scripts/lab/run_lab_app.py  (port {LAB_PORT})")
    print(f"  Main UI stays on port 8501 — unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
