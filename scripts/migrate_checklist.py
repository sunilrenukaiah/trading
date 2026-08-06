"""One-time migration checklist for a new machine (Windows / macOS / Linux)."""

from __future__ import annotations

import sys
from pathlib import Path

from ide_setup import check_ide_compatibility, configure_ide, ide_next_steps, normalize_ide
from platform_utils import (
    ROOT,
    create_venv,
    ensure_env_file,
    install_python_packages,
    manual_steps_summary,
    port_open,
    print_header,
    print_step,
    run_migrations,
)


def run_migrate_checklist(ide: str = "cursor") -> int:
    ide = normalize_ide(ide)
    print_header("NIFTY Paper Trading — migration setup")
    print(f"  Platform: {sys.platform}")
    print(f"  IDE:      {ide}")
    print(f"  Project:  {ROOT}")

    failures: list[str] = []
    manual: list[str] = []
    req_file = ROOT / "requirements-migrate.txt"

    # Python version
    ver = sys.version_info
    ok = ver >= (3, 11)
    print_step(ok, f"Python {ver.major}.{ver.minor}.{ver.micro} (need 3.11+)")
    if not ok:
        failures.append("Install Python 3.11+ and re-run Setup.py")

    # Virtual environment
    ok, msg = create_venv()
    print_step(ok, "Virtual environment", detail=msg)
    if not ok:
        failures.append(msg)

    # Pip packages
    if req_file.exists():
        ok, msg = install_python_packages(req_file)
        print_step(ok, "Python packages (requirements-migrate.txt)", detail=msg)
        if not ok:
            failures.append(msg)
    else:
        print_step(False, "requirements-migrate.txt missing")
        failures.append("Missing requirements-migrate.txt")

    # .env
    ok, msg = ensure_env_file()
    print_step(ok, "Environment file backend/.env", detail=msg)

    # PostgreSQL
    pg_ok = port_open("localhost", 5432)
    if pg_ok:
        print_step(True, "PostgreSQL reachable on localhost:5432")
    else:
        print_step(False, "PostgreSQL not reachable on localhost:5432")
        manual.append(
            "Install PostgreSQL 15+ and ensure localhost:5432 is running (see docs/MIGRATION.md)"
        )

    if pg_ok:
        ok, msg = run_migrations()
        print_step(ok, "Database migrations (alembic upgrade head)", detail=msg)
        if not ok:
            failures.append(msg)
    else:
        manual.append(
            "After Postgres is running, run: cd backend && .venv\\Scripts\\python -m alembic upgrade head"
        )
        print_step(False, "Database migrations", detail="Skipped — Postgres unavailable")

    # IDE compatibility + configuration
    print_header(f"IDE compatibility — {ide}")
    ide_ok, ide_checks = check_ide_compatibility(ide)
    for step_ok, label, detail in ide_checks:
        print_step(step_ok, label, detail=detail)
    if not ide_ok:
        failures.append(f"{ide} compatibility check failed — fix venv/imports above")

    if ide_ok:
        configured, ide_msg = configure_ide(ide)
        print_step(configured, f"Configure {ide}", detail=ide_msg)
        if not configured:
            failures.append(ide_msg)

    # Summary
    print_header("Migration summary")
    if failures:
        print("  Automatic setup completed with errors:")
        for item in failures:
            print(f"    - {item}")
    else:
        print("  All automated steps succeeded.")

    if manual:
        print()
        print("  Manual follow-up:")
        for item in manual:
            print(f"    - {item}")

    print()
    print(manual_steps_summary(ide=ide))
    print()
    print(ide_next_steps(ide))
    print()
    if failures or manual:
        print("  Next: fix items above, then run: python scripts/run_app.py")
        return 1
    print("  Next: python scripts/run_app.py  (or use IDE run configuration)")
    print("  UI: http://localhost:8501")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    target = sys.argv[1] if len(sys.argv) > 1 else "cursor"
    raise SystemExit(run_migrate_checklist(target))
