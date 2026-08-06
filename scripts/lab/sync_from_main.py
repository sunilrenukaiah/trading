#!/usr/bin/env python3
"""Pull main (8501) code + PostgreSQL data into trading-lab — main is never modified."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LAB_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_SCRIPTS.parent))

from lab.config import LAB_SCHEMA, read_lab_meta, resolve_paths, write_lab_meta  # noqa: E402
from lab.create_backup import (  # noqa: E402
    _clone_database,
    _ensure_lab_env,
    _rsync_copy,
)
from lab.pg_tools import parse_db_url  # noqa: E402
from platform_utils import load_database_url, venv_python  # noqa: E402

# Lab-only Phase 1 artifacts — kept when refreshing from main.
PHASE1_PRESERVE = [
    "backend/alembic/versions/009_eod_analysis_cache.py",
    "backend/alembic/versions/008_background_jobs.py",
    "backend/app/jobs/worker.py",
    "backend/app/jobs/handlers.py",
    "backend/app/jobs/progress.py",
    "backend/app/services/sim_backtest_job.py",
    "backend/app/services/recommendation_job.py",
    "backend/app/services/market_sync_job.py",
    "backend/app/services/eod_analysis_job.py",
    "backend/app/services/eod_analysis_cache.py",
    "backend/app/services/job_queue.py",
    "backend/ui/background_jobs.py",
    "backend/ui/async_runner.py",
    "backend/ui/streamlit_imports.py",
    "backend/tests/conftest.py",
    "backend/tests/integration/test_job_queue.py",
    "backend/tests/integration/test_worker.py",
    "backend/tests/integration/test_sim_backtest_job.py",
    "backend/tests/integration/test_recommendation_job.py",
    "backend/tests/integration/test_market_sync_job.py",
    "backend/tests/integration/test_eod_analysis_job.py",
    "backend/tests/integration/test_background_jobs.py",
    "backend/tests/integration/test_audit_error_handling.py",
    "backend/tests/integration/test_dashboard_import_contract.py",
]

BACKGROUND_JOB_MARKER = "class BackgroundJob(Base):"


def _backup_files(lab_root: Path, rel_paths: list[str]) -> dict[str, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="lab-preserve-"))
    saved: dict[str, Path] = {}
    for rel in rel_paths:
        src = lab_root / rel
        if src.is_file():
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            saved[rel] = dest
    models = lab_root / "backend/app/models/__init__.py"
    if models.is_file() and BACKGROUND_JOB_MARKER in models.read_text(encoding="utf-8"):
        dest = tmp / "backend/app/models/__init__.py.phase1"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(models, dest)
        saved["backend/app/models/__init__.py.phase1"] = dest
    saved["_tmpdir"] = tmp
    return saved


def _restore_files(lab_root: Path, saved: dict[str, Path]) -> None:
    for rel, src in saved.items():
        if rel == "_tmpdir":
            continue
        if rel.endswith(".phase1"):
            continue
        dest = lab_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _extract_background_job_block(backup_text: str) -> str | None:
    start = backup_text.find(BACKGROUND_JOB_MARKER)
    if start < 0:
        return None
    rest = backup_text[start:]
    end = rest.find("\n\nclass ", len(BACKGROUND_JOB_MARKER))
    if end > 0:
        return rest[:end].strip()
    end = rest.find("\n\nfrom app.models.audit_log")
    if end > 0:
        return rest[:end].strip()
    return rest.strip()


def _merge_background_job_model(lab_root: Path, saved: dict[str, Path]) -> None:
    models_path = lab_root / "backend/app/models/__init__.py"
    if not models_path.is_file():
        return
    text = models_path.read_text(encoding="utf-8")
    if BACKGROUND_JOB_MARKER in text:
        return
    backup_key = "backend/app/models/__init__.py.phase1"
    if backup_key not in saved:
        return
    block = _extract_background_job_block(saved[backup_key].read_text(encoding="utf-8"))
    if not block:
        return
    needle = "from app.models.audit_log import AuditLog"
    if needle in text:
        text = text.replace(needle, f"{block}\n\n\n{needle}", 1)
    else:
        text = text.rstrip() + "\n\n\n" + block + "\n"
    models_path.write_text(text, encoding="utf-8")
    print("  Merged BackgroundJob model into lab models/__init__.py")


def _run_alembic_upgrade(lab_backend: Path) -> None:
    py = lab_backend / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(venv_python())

    def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(py), "-m", "alembic", *args],
            cwd=lab_backend,
            capture_output=True,
            text=True,
        )

    result = _alembic("upgrade", "head")
    if result.returncode == 0:
        print(result.stdout.strip() or "  alembic upgrade head OK")
        return
    if "background_jobs" in (result.stderr or "") and "already exists" in (result.stderr or ""):
        stamp = _alembic("stamp", "head")
        if stamp.returncode == 0:
            print("  background_jobs already present — stamped alembic head")
            return
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    raise SystemExit(f"alembic upgrade failed ({result.returncode})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync main trading (8501) -> trading-lab (8502); main unchanged",
    )
    parser.add_argument("--skip-db", action="store_true", help="Code only; skip PostgreSQL clone")
    parser.add_argument("--skip-code", action="store_true", help="DB only; skip code rsync")
    parser.add_argument("--skip-tests", action="store_true", help="Skip lab verify after sync")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    paths = resolve_paths()
    if not paths.lab_root.is_dir():
        print(f"Lab root missing: {paths.lab_root}")
        print("Run: python scripts/lab/create_backup.py")
        return 1

    if not args.yes:
        print("This copies code and DB data FROM main INTO trading-lab.")
        print("Main (8501 / public schema) is NOT modified.")
        answer = input("Type 'pull' to continue: ").strip()
        if answer != "pull":
            print("Aborted.")
            return 1

    source_url = load_database_url()
    conn = parse_db_url(source_url)

    print("=" * 60)
    print("Sync main -> trading-lab")
    print("=" * 60)
    print(f"  Main:  {paths.main_root}  (port 8501)")
    print(f"  Lab:   {paths.lab_root}  (port 8502)")
    print(f"  DB:    {conn.database}.public -> {conn.database}.{LAB_SCHEMA}")
    print()

    saved = _backup_files(paths.lab_root, PHASE1_PRESERVE)
    try:
        if not args.skip_code:
            print("[1/5] Copying main code -> lab (preserving Phase 1 job queue)…")
            _rsync_copy(paths.main_root, paths.lab_root)
            lab_scripts = paths.lab_root / "scripts" / "lab"
            shutil.copytree(LAB_SCRIPTS, lab_scripts, dirs_exist_ok=True)
            _restore_files(paths.lab_root, saved)
            _merge_background_job_model(paths.lab_root, saved)
        else:
            print("[1/5] Skipped code sync")

        if not args.skip_db:
            print("[2/5] Cloning PostgreSQL public -> trading_lab…")
            _clone_database(source_url, paths.db_dumps_dir)
        else:
            print("[2/5] Skipped database clone")

        print("[3/5] Refreshing lab environment…")
        _ensure_lab_env(paths.lab_backend, source_url)

        print("[4/5] Applying lab migrations (incl. Phase 1 background_jobs)…")
        _run_alembic_upgrade(paths.lab_backend)

        print("[5/5] Updating lab metadata…")
        meta = read_lab_meta(paths) or {}
        meta["last_sync_from_main"] = datetime.now(timezone.utc).isoformat()
        meta["source_root"] = str(paths.main_root)
        paths.meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        write_lab_meta(
            paths,
            source_db=conn.database,
            notes="Refreshed from main; Phase 1 job queue preserved in lab.",
        )

        if not args.skip_tests:
            print()
            print("Running lab verification…")
            verify = subprocess.run(
                [str(venv_python()), str(LAB_SCRIPTS / "verify_lab.py")],
                cwd=paths.main_root,
                check=False,
            )
            if verify.returncode != 0:
                print("Lab verification FAILED — review lab copy")
                return verify.returncode

        print()
        print("Sync from main complete.")
        print(f"  Lab UI: python scripts/lab/run_lab_app.py  -> http://localhost:8502")
        print("  Main UI unchanged on port 8501")
        return 0
    finally:
        tmp = saved.get("_tmpdir")
        if tmp and tmp.is_dir():
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
