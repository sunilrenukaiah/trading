"""Startup health checklist — run before each app session."""

from __future__ import annotations

import sys
from pathlib import Path

from platform_utils import (
    BACKEND,
    ROOT,
    VENV_DIR,
    check_database_connect,
    check_import,
    check_python_version,
    ensure_env_file,
    filter_pip_lines,
    parse_checklist,
    parse_pg_host_port,
    port_free,
    port_open,
    load_database_url,
    print_header,
    print_step,
    run_migrations,
    venv_python,
)


def run_startup_checklist(
    *,
    strict: bool = True,
    run_migrate: bool = True,
    checklist: Path | None = None,
) -> int:
    checklist_path = checklist or ROOT / "requirements-start.txt"
    print_header("Startup health check")

    if not checklist_path.exists():
        print_step(False, checklist_path.name + " missing")
        return 1 if strict else 0

    checks = parse_checklist(checklist_path)
    failures: list[str] = []
    warnings: list[str] = []

    for check_type, arg, required in checks:
        ok = False
        detail = ""

        if check_type == "python_version":
            parts = arg.split(".")
            major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            ok, detail = check_python_version(major, minor)
            print_step(ok, f"Python>={arg}", detail=detail)

        elif check_type == "venv":
            venv_path = ROOT / arg
            ok = venv_path.exists() and venv_python().exists()
            detail = str(venv_path) if ok else f"Missing {venv_path} — run: python Setup.py"
            print_step(ok, "Virtual environment", detail=detail)

        elif check_type == "pip_packages":
            req = ROOT / arg
            if not req.exists():
                ok, detail = False, f"{arg} not found"
            else:
                ok, detail = True, f"{len(filter_pip_lines(req))} packages listed"
                for pkg in ("streamlit", "asyncpg", "sqlalchemy"):
                    import_ok, import_msg = check_import(pkg)
                    if not import_ok:
                        ok = False
                        detail = import_msg
                        break
            print_step(ok, "Python packages importable", detail=detail)

        elif check_type == "env_file":
            env = ROOT / arg
            if env.exists():
                ok, detail = True, str(env)
            else:
                created_ok, msg = ensure_env_file()
                ok, detail = created_ok, msg
            print_step(ok, "Environment file", detail=detail)

        elif check_type == "postgres":
            host, port = arg.split(":") if ":" in arg else (arg, "5432")
            ok = port_open(host, int(port))
            detail = f"{host}:{port}" if ok else f"Cannot reach {host}:{port}"
            print_step(ok, "PostgreSQL port", detail=detail)

        elif check_type == "database":
            ok, detail = check_database_connect()
            print_step(ok, "Database connection", detail=detail[:200])

        elif check_type == "migrations":
            if run_migrate:
                ok, detail = run_migrations()
            else:
                ok, detail = True, "Skipped (use run_app.py for migrate-on-start)"
            print_step(ok, "Alembic migrations", detail=detail[:200])

        elif check_type == "port_free":
            ok = port_free(int(arg))
            detail = f"Port {arg} is free" if ok else f"Port {arg} in use (Streamlit may still start)"
            print_step(ok, f"Port {arg} available", detail=detail)
            if not ok and not required:
                ok = True  # warning only

        elif check_type == "instance_guard":
            # argument: main:8501 or lab:8502
            instance, port_s = arg.split(":", 1)
            from instance_guard import run_instance_guard

            # Capture printed guard output only on failure (run_instance_guard prints always)
            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = run_instance_guard(
                    instance=instance,
                    expected_port=int(port_s),
                    strict=True,
                )
            ok = code == 0
            detail = "isolated" if ok else buf.getvalue().strip().splitlines()[-1]

        elif check_type == "streamlit":
            ok, detail = check_import("streamlit")
            print_step(ok, "Streamlit import", detail=detail)

        else:
            detail = f"Unknown check type: {check_type}"
            print_step(False, check_type, detail=detail)
            ok = False

        if not ok:
            (failures if required else warnings).append(f"{check_type}: {detail}")

    print_header("Startup check summary")
    if failures:
        for item in failures:
            print(f"  [FAIL] {item}")
    if warnings:
        for item in warnings:
            print(f"  [WARN] {item}")
    if not failures and not warnings:
        print("  All checks passed.")
    elif not failures:
        print("  Required checks passed (with warnings).")

    if failures:
        print()
        print("  Fix failures, or run one-time setup: python Setup.py")
        db_url = load_database_url()
        host, port = parse_pg_host_port(db_url)
        if not port_open(host, port):
            print(
                f"  PostgreSQL expected at {host}:{port} — start PostgreSQL and verify backend/.env"
            )
        return 1 if strict else 0
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    strict = "--warn-only" not in sys.argv
    raise SystemExit(run_startup_checklist(strict=strict))
