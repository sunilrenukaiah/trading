"""Paths and ports for the isolated trading-lab copy."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAIN_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LAB_ROOT = MAIN_ROOT.parent / "trading-lab"

MAIN_PORT = 8501
LAB_PORT = 8502
MAIN_API_PORT = 8000
LAB_API_PORT = 8001

MAIN_DB_NAME = "trading"
LAB_DB_NAME = "trading_lab"
LAB_SCHEMA = "trading_lab"

META_FILENAME = ".lab-meta.json"


@dataclass(frozen=True)
class LabPaths:
    main_root: Path
    lab_root: Path

    @property
    def main_backend(self) -> Path:
        return self.main_root / "backend"

    @property
    def lab_backend(self) -> Path:
        return self.lab_root / "backend"

    @property
    def meta_path(self) -> Path:
        return self.lab_root / META_FILENAME

    @property
    def db_dumps_dir(self) -> Path:
        return self.lab_root / ".lab-dumps"


def resolve_paths() -> LabPaths:
    lab_root = Path(os.environ.get("TRADING_LAB_ROOT", str(DEFAULT_LAB_ROOT))).resolve()
    return LabPaths(main_root=MAIN_ROOT, lab_root=lab_root)


def write_lab_meta(paths: LabPaths, *, source_db: str, notes: str = "") -> None:
    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(paths.main_root),
        "lab_root": str(paths.lab_root),
        "source_database": source_db,
        "lab_database": LAB_DB_NAME,
        "main_port": MAIN_PORT,
        "lab_port": LAB_PORT,
        "notes": notes,
    }
    paths.lab_root.mkdir(parents=True, exist_ok=True)
    paths.meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def read_lab_meta(paths: LabPaths) -> dict | None:
    if not paths.meta_path.exists():
        return None
    return json.loads(paths.meta_path.read_text(encoding="utf-8"))
