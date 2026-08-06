#!/usr/bin/env python3
"""
One-time setup when migrating this project to a new machine (Windows / macOS / Linux).

Usage (from repo root):
    python Setup.py              # default: Cursor
    python Setup.py cursor
    python Setup.py pycharm      # PyCharm Community Edition on Windows

What it does:
  - Creates backend/.venv
  - Installs Python packages from requirements-migrate.txt
  - Creates backend/.env if missing
  - Runs Alembic migrations (when PostgreSQL is reachable)
  - Configures IDE (Cursor: .vscode/ | PyCharm: .idea/runConfigurations/)
  - Prints manual steps for anything it cannot automate

After setup, start the app with:
    python scripts/run_app.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from ide_setup import IDE_CHOICES, normalize_ide  # noqa: E402
from migrate_checklist import run_migrate_checklist  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-time migration setup for NIFTY Paper Trading",
    )
    parser.add_argument(
        "ide",
        nargs="?",
        choices=IDE_CHOICES,
        default="cursor",
        help="Target IDE: cursor (default) or pycharm (Community Edition)",
    )
    parser.add_argument(
        "--ide",
        dest="ide_flag",
        choices=IDE_CHOICES,
        help="Same as positional IDE argument",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    ide = normalize_ide(args.ide_flag or args.ide)
    raise SystemExit(run_migrate_checklist(ide))
