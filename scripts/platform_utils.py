"""Shared helpers for setup and startup checklists."""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
VENV_DIR = BACKEND / ".venv"
IS_WINDOWS = platform.system() == "Windows"
PYTHON = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
PIP = VENV_DIR / ("Scripts/pip.exe" if IS_WINDOWS else "bin/pip")


def venv_python() -> Path:
    return PYTHON if PYTHON.exists() else Path(sys.executable)


def print_header(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def print_step(ok: bool, message: str, *, detail: str = "") -> None:
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {message}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"         {line}")


def run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd or ROOT,
        check=check,
        capture_output=capture,
        text=True,
    )


def parse_checklist(path: Path) -> list[tuple[str, str, bool]]:
    """Parse requirements-start.txt into (check_type, argument, required)."""
    rows: list[tuple[str, str, bool]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        check_type, arg = parts[0], parts[1]
        required = True
        if len(parts) >= 3:
            required = parts[2].lower() in ("yes", "true", "1", "required")
        rows.append((check_type, arg, required))
    return rows


def filter_pip_lines(requirements_path: Path) -> list[str]:
    packages: list[str] = []
    for raw in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        packages.append(line)
    return packages


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_free(port: int) -> bool:
    return not port_open("127.0.0.1", port)


def load_database_url() -> str:
    env_path = BACKEND / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://trading:trading@localhost:5432/trading",
    )


def parse_pg_host_port(url: str) -> tuple[str, int]:
    # postgresql+asyncpg://user:pass@host:port/db
    match = re.search(r"@([^:/]+)(?::(\d+))?", url)
    if not match:
        return "localhost", 5432
    host = match.group(1)
    port = int(match.group(2) or 5432)
    return host, port


def ensure_env_file() -> tuple[bool, str]:
    env_path = BACKEND / ".env"
    examples = [BACKEND / ".env.example", BACKEND / "env.example", ROOT / "env.example"]
    if env_path.exists():
        return True, str(env_path)
    for example in examples:
        if example.exists():
            env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            return True, f"Created {env_path} from {example.name}"
    template = (
        "DATABASE_URL=postgresql+asyncpg://trading:trading@localhost:5432/trading\n"
        "PAPER_STARTING_CASH=1000000\n"
        "DATA_PROVIDER=nse\n"
        "BACKFILL_DAYS=120\n"
    )
    env_path.write_text(template, encoding="utf-8")
    return True, f"Created {env_path} with default values"


def create_venv() -> tuple[bool, str]:
    if VENV_DIR.exists() and PYTHON.exists():
        return True, str(VENV_DIR)
    result = run_cmd([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "venv creation failed").strip()
    return True, str(VENV_DIR)


def install_python_packages(requirements_path: Path) -> tuple[bool, str]:
    ok, msg = create_venv()
    if not ok:
        return False, msg
    py = str(venv_python())
    pip_args = [py, "-m", "pip", "install", "--upgrade", "pip"]
    run_cmd(pip_args)
    packages = filter_pip_lines(requirements_path)
    if not packages:
        return False, f"No packages found in {requirements_path}"
    install = [py, "-m", "pip", "install"] + packages
    result = run_cmd(install, cwd=BACKEND)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "pip install failed").strip()
    editable = run_cmd([py, "-m", "pip", "install", "-e", "."], cwd=BACKEND)
    if editable.returncode != 0:
        return False, (editable.stderr or editable.stdout or "pip install -e failed").strip()
    return True, f"Installed {len(packages)} packages + editable backend"


def run_migrations() -> tuple[bool, str]:
    py = str(venv_python())
    result = run_cmd([py, "-m", "alembic", "upgrade", "head"], cwd=BACKEND)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "alembic upgrade failed").strip()
    return True, "Database migrations at head"


def check_python_version(min_major: int, min_minor: int) -> tuple[bool, str]:
    ver = sys.version_info
    ok = (ver.major, ver.minor) >= (min_major, min_minor)
    return ok, f"{ver.major}.{ver.minor}.{ver.micro}"


def check_import(module: str) -> tuple[bool, str]:
    py = str(venv_python())
    code = f"import importlib; importlib.import_module({module!r})"
    result = run_cmd([py, "-c", code], cwd=BACKEND)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or f"cannot import {module}").strip()
    return True, module


def check_database_connect() -> tuple[bool, str]:
    py = str(venv_python())
    script = """
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings

async def main():
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    await engine.dispose()

asyncio.run(main())
"""
    result = run_cmd([py, "-c", script], cwd=BACKEND)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "database connection failed").strip()
    return True, "Connected to PostgreSQL"


def manual_steps_summary(*, ide: str = "cursor") -> str:
    ide = ide.lower()
    ide_name = "PyCharm Community Edition" if ide == "pycharm" else "Cursor"
    setup_cmd = f"python Setup.py {ide}"
    lines = [
        "Manual steps (if automatic setup could not complete everything):",
        "",
        "1. Install Python 3.11+",
        "   Windows: https://www.python.org/downloads/ — enable 'Add python.exe to PATH'",
        "",
        "2. Install PostgreSQL 15+",
        "   Windows: https://www.postgresql.org/download/windows/",
        "   Create user/db: trading / trading / database name trading",
        "",
        f"3. Open project in {ide_name} (folder: trading repo root)",
        "",
        "4. Run one-time setup from repo root:",
        f"     {setup_cmd}",
        "",
        "5. Start the app:",
        "     python scripts/run_app.py",
        "   Or use the IDE run configuration (Streamlit Dashboard)",
        "   UI: http://localhost:8501",
        "",
        "6. Optional REST API:",
        "     cd backend && .venv\\Scripts\\activate",
        "     uvicorn app.main:app --reload --port 8000",
        "",
        "IDE docs:",
        f"  - {'docs/PYCHARM.md' if ide == 'pycharm' else 'docs/MIGRATION.md'}",
        "",
        "Services used by this project:",
        "  - PostgreSQL 15+ (required) — stores candles, orders, recommendations",
        "  - Streamlit (Python package) — main UI on port 8501",
        "  - FastAPI + Uvicorn (optional) — REST API on port 8000",
        "  - NSE network access — market data sync & live quotes (no API key for paper mode)",
    ]
    return "\n".join(lines)
