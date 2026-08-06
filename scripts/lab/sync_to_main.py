#!/usr/bin/env python3
"""Promote trading-lab changes to main after tests pass. Run only when you say 'sync'."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_SCRIPTS.parent))

from lab.config import (  # noqa: E402
    LAB_DB_NAME,
    META_FILENAME,
    read_lab_meta,
    resolve_paths,
)
from lab.create_backup import RSYNC_EXCLUDES, _run  # noqa: E402
from lab.pg_tools import parse_db_url, psql  # noqa: E402
from platform_utils import load_database_url, venv_python  # noqa: E402

SYNC_EXcludes = RSYNC_EXCLUDES + [
    META_FILENAME,
    "backend/.env",
]


def _rsync_to_main(lab_root: Path, main_root: Path) -> None:
    if shutil.which("rsync"):
        cmd = ["rsync", "-a"]
        for item in SYNC_EXcludes:
            cmd.extend(["--exclude", item])
        cmd.extend([f"{lab_root}/", f"{main_root}/"])
        _run(cmd)
        return

    raise SystemExit("sync requires rsync on PATH for safe directory merge")


def _promote_database(*, include_db: bool) -> None:
    if not include_db:
        print("  Skipping database promote (--include-db not set)")
        return

    from lab.config import LAB_SCHEMA  # noqa: E402

    conn = parse_db_url(load_database_url())
    print(f"  Promoting schema {LAB_SCHEMA} -> public (table data overwrite)")
    tables_sql = (
        f"SELECT tablename FROM pg_tables WHERE schemaname = '{LAB_SCHEMA}' ORDER BY tablename;"
    )
    if conn.docker_container:
        list_cmd = [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={conn.password}",
            conn.docker_container,
            "psql",
            "-U",
            conn.user,
            "-d",
            conn.database,
            "-tAc",
            tables_sql,
        ]
    else:
        raise SystemExit("Schema promote requires Docker PostgreSQL or psql on PATH")

    result = subprocess.run(list_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or "Failed to list lab tables")

    tables = [t.strip() for t in result.stdout.splitlines() if t.strip()]
    for table in tables:
        sql = (
            f"TRUNCATE public.{table} CASCADE; "
            f"INSERT INTO public.{table} SELECT * FROM {LAB_SCHEMA}.{table};"
        )
        psql(conn, conn.database, sql)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync trading-lab -> main (after your approval)")
    parser.add_argument(
        "--include-db",
        action="store_true",
        help="Also replace main PostgreSQL trading DB with trading_lab (destructive)",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip main test suite after sync (not recommended)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    paths = resolve_paths()
    meta = read_lab_meta(paths)
    if meta is None:
        print("Lab metadata not found — run create_backup.py first")
        return 1

    if not args.yes:
        print("This will copy code from trading-lab into the main trading project.")
        if args.include_db:
            print("WARNING: --include-db will OVERWRITE the main trading database.")
        answer = input("Type 'sync' to continue: ").strip()
        if answer != "sync":
            print("Aborted.")
            return 1

    print("[1/4] Running lab verification…")
    verify = subprocess.run(
        [str(venv_python()), str(LAB_SCRIPTS / "verify_lab.py")],
        cwd=paths.main_root,
        check=False,
    )
    if verify.returncode != 0:
        print("Lab verification failed — sync aborted")
        return verify.returncode

    print("[2/4] Copying lab code -> main…")
    _rsync_to_main(paths.lab_root, paths.main_root)

    print("[3/4] Promoting database (optional)…")
    _promote_database(include_db=args.include_db)

    if not args.skip_tests:
        print("[4/4] Running main test suite…")
        test_script = paths.main_root / "backend" / "scripts" / "run_tests.sh"
        result = subprocess.run([str(test_script), "all"], cwd=paths.main_root / "backend")
        if result.returncode != 0:
            print("Main tests FAILED after sync — review changes")
            return result.returncode
    else:
        print("[4/4] Skipped main tests")

    log = paths.main_root / ".cursor" / "lab-sync-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "from": str(paths.lab_root),
        "include_db": args.include_db,
        "lab_meta": meta,
    }
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

    print()
    print("Sync complete. Restart main app: python scripts/run_app.py (port 8501)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
