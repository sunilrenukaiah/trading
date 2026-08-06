#!/usr/bin/env python3
"""Create or refresh trading-lab: code copy + isolated PostgreSQL database."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

LAB_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LAB_SCRIPTS.parent))

from lab.config import (  # noqa: E402
    LAB_DB_NAME,
    LAB_PORT,
    MAIN_PORT,
    write_lab_meta,
    resolve_paths,
)
from platform_utils import load_database_url, parse_pg_host_port, venv_python  # noqa: E402

RSYNC_EXCLUDES = [
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cursor",
    ".idea",
    "node_modules",
    ".lab-dumps",
    ".lab-meta.json",
]


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


from lab.config import LAB_SCHEMA  # noqa: E402
from lab.pg_tools import clone_public_to_schema, parse_db_url  # noqa: E402


def _rsync_copy(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if shutil.which("rsync"):
        cmd = ["rsync", "-a", "--delete"]
        for item in RSYNC_EXCLUDES:
            cmd.extend(["--exclude", item])
        cmd.extend([f"{source}/", f"{dest}/"])
        _run(cmd)
        return

    if dest.exists():
        for child in dest.iterdir():
            if child.name in {".lab-dumps", ".lab-meta.json"}:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in RSYNC_EXCLUDES}

    shutil.copytree(source, dest, dirs_exist_ok=True, ignore=_ignore)


def _ensure_lab_env(lab_backend: Path, source_url: str) -> None:
    conn = parse_db_url(source_url)
    # Same database name; LAB_SCHEMA + LAB_MODE route queries to trading_lab schema.
    lab_db_url = (
        f"postgresql+asyncpg://{conn.user}:{conn.password}@{conn.host}:{conn.port}/{conn.database}"
    )

    env_path = lab_backend / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    def _set(key: str, value: str) -> None:
        nonlocal lines
        prefix = f"{key}="
        replaced = False
        out: list[str] = []
        for line in lines:
            if line.strip().startswith(prefix):
                out.append(f"{prefix}{value}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"{prefix}{value}")
        lines = out

    _set("DATABASE_URL", lab_db_url)
    _set("LAB_MODE", "1")
    _set("LAB_SCHEMA", LAB_SCHEMA)
    _set("LAB_ISOLATION", "schema")
    _set("STREAMLIT_SERVER_PORT", str(LAB_PORT))
    _set("TRADING_UI_PORT", str(LAB_PORT))
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_lab_venv(main_backend: Path, lab_backend: Path) -> None:
    """Dedicated lab venv — never symlink main (shared editable installs cross-contaminate)."""
    import subprocess

    lab_venv = lab_backend / ".venv"
    if lab_venv.is_symlink():
        print("  Removing shared lab venv symlink")
        lab_venv.unlink()

    if lab_venv.exists():
        pip = lab_venv / "bin" / "pip"
        if pip.exists():
            print("  Ensuring lab editable install points at lab backend")
            subprocess.run(
                [str(pip), "install", "-e", str(lab_backend)],
                check=True,
                capture_output=True,
                text=True,
            )
        return

    py = shutil.which("python3") or sys.executable
    print("  Creating isolated lab virtualenv")
    subprocess.run([py, "-m", "venv", str(lab_venv)], check=True)
    pip = lab_venv / "bin" / "pip"
    req = lab_backend.parent / "requirements-migrate.txt"
    if not req.exists():
        req = main_backend.parent / "requirements-migrate.txt"
    if req.exists():
        subprocess.run([str(pip), "install", "-r", str(req)], check=True)
    subprocess.run([str(pip), "install", "-e", str(lab_backend)], check=True)


def _clone_database(source_url: str, dumps_dir: Path) -> None:
    conn = parse_db_url(source_url)
    dumps_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_file = dumps_dir / f"{LAB_SCHEMA}_{stamp}.sql"

    if conn.docker_container:
        print(f"  Using Docker container: {conn.docker_container}")
    dump_sql = clone_public_to_schema(conn, LAB_SCHEMA)
    dump_file.write_bytes(dump_sql)

    latest = dumps_dir / "latest.sql"
    shutil.copy2(dump_file, latest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or refresh trading-lab backup")
    parser.add_argument("--skip-db", action="store_true", help="Copy code only; skip PostgreSQL clone")
    parser.add_argument("--skip-code", action="store_true", help="Refresh DB only")
    args = parser.parse_args()

    paths = resolve_paths()
    source_url = load_database_url()
    conn = parse_db_url(source_url)

    print("=" * 60)
    print("Trading lab backup")
    print("=" * 60)
    print(f"  Main:  {paths.main_root}")
    print(f"  Lab:   {paths.lab_root}")
    print(f"  DB:    {conn.database}.public -> {conn.database}.{LAB_SCHEMA}")
    print(f"  Ports: main {MAIN_PORT} | lab {LAB_PORT}")
    print()

    if not args.skip_code:
        print("[1/4] Copying project files…")
        _rsync_copy(paths.main_root, paths.lab_root)
        lab_scripts = paths.lab_root / "scripts" / "lab"
        shutil.copytree(LAB_SCRIPTS, lab_scripts, dirs_exist_ok=True)
        docs_lab = paths.lab_root / "docs" / "lab"
        docs_lab.mkdir(parents=True, exist_ok=True)
        main_plan = paths.main_root / "docs" / "lab" / "AGENT_ARCHITECTURE_PLAN.md"
        if main_plan.exists():
            shutil.copy2(main_plan, docs_lab / main_plan.name)

    if not args.skip_db:
        print("[2/4] Cloning PostgreSQL database…")
        _clone_database(source_url, paths.db_dumps_dir)

    print("[3/4] Writing lab environment…")
    _ensure_lab_env(paths.lab_backend, source_url)
    _ensure_lab_venv(paths.main_backend, paths.lab_backend)

    print("[4/4] Recording metadata…")
    write_lab_meta(
        paths,
        source_db=conn.database,
        notes="Schema-isolated backup — experiments run in trading-lab only until sync.",
    )

    print()
    print("Backup complete.")
    print(f"  Lab UI:  python scripts/lab/run_lab_app.py  -> http://localhost:{LAB_PORT}")
    print(f"  Verify:  python scripts/lab/verify_lab.py")
    print(f"  Sync:    python scripts/lab/sync_to_main.py   (when you approve)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
