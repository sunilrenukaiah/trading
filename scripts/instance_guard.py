"""Guardrails so main (8501) and lab (8502) never share code, venv, or DB schema."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platform_utils import BACKEND, ROOT, VENV_DIR, load_database_url


def _path_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_editable_roots() -> dict[str, Path]:
    """Return on-disk roots for editable ``app`` and ``ui`` packages."""
    roots: dict[str, Path] = {}
    finder_path = None
    for site in sys.path:
        candidate = Path(site) / "__editable___trading_backend_0_1_0_finder.py"
        if candidate.exists():
            finder_path = candidate
            break
    if finder_path is not None:
        text = finder_path.read_text(encoding="utf-8")
        for pkg in ("app", "ui"):
            needle = f"'{pkg}': '"
            start = text.find(needle)
            if start == -1:
                continue
            start += len(needle)
            end = text.find("'", start)
            if end != -1:
                roots[pkg] = Path(text[start:end]).resolve()
    if not roots:
        import app.config  # noqa: WPS433
        import ui.helpers  # noqa: WPS433

        roots["app"] = Path(app.config.__file__).resolve().parent.parent
        roots["ui"] = Path(ui.helpers.__file__).resolve().parent
    return roots


def verify_code_root(*, expected_backend: Path | None = None) -> tuple[bool, str]:
    """``app``/``ui`` must resolve under the project's backend directory."""
    backend = (expected_backend or BACKEND).resolve()
    roots = resolve_editable_roots()
    mismatches: list[str] = []
    for pkg, root in roots.items():
        if not _path_under(root, backend):
            mismatches.append(f"{pkg} -> {root} (expected under {backend})")
    if mismatches:
        return False, "; ".join(mismatches)
    return True, str(backend)


def verify_lab_mode(*, expect_lab: bool) -> tuple[bool, str]:
    """Main must not run with LAB_MODE=1; lab must use LAB_MODE=1."""
    raw = os.environ.get("LAB_MODE", "")
    lab_on = raw.strip().lower() in {"1", "true", "yes", "on"}
    if expect_lab and not lab_on:
        return False, "LAB_MODE must be 1 for the lab instance (port 8502)"
    if not expect_lab and lab_on:
        return False, "LAB_MODE=1 is set — this is the lab config; use port 8502, not 8501"
    return True, "lab" if lab_on else "main"


def verify_venv_isolation(*, expect_lab: bool) -> tuple[bool, str]:
    """Lab and main must not share the same virtualenv directory."""
    venv = VENV_DIR.resolve()
    if not venv.exists():
        return True, "venv missing (setup will create one)"

    if expect_lab:
        main_venv = (ROOT.parent / "trading" / "backend" / ".venv").resolve()
        if main_venv.exists() and venv == main_venv:
            return False, f"Lab venv is the main venv ({venv}) — run create_backup.py to fix"
        if venv.is_symlink():
            target = venv.resolve()
            if target == main_venv:
                return False, f"Lab .venv symlinks to main ({target})"

    return True, str(venv)


def verify_database_isolation(*, expect_lab: bool) -> tuple[bool, str]:
    """Lab queries must use a dedicated PostgreSQL schema."""
    if not expect_lab:
        schema = os.environ.get("LAB_SCHEMA", "").strip()
        if schema:
            return False, f"LAB_SCHEMA={schema} on main instance — remove from backend/.env"
        return True, "public schema (main)"

    schema = os.environ.get("LAB_SCHEMA", "").strip()
    if not schema or schema.lower() == "public":
        return False, "LAB_SCHEMA must be set to a non-public schema for lab"
    return True, f"schema {schema}"


def verify_ui_port(*, expected_port: int) -> tuple[bool, str]:
    """Optional guard when TRADING_UI_PORT is set."""
    raw = os.environ.get("TRADING_UI_PORT", "").strip()
    if not raw:
        return True, f"port check skipped (expected {expected_port})"
    try:
        port = int(raw)
    except ValueError:
        return False, f"TRADING_UI_PORT={raw!r} is not an integer"
    if port != expected_port:
        return False, f"TRADING_UI_PORT={port} but this launcher expects {expected_port}"
    return True, str(port)


def run_instance_guard(
    *,
    instance: str,
    expected_port: int,
    strict: bool = True,
) -> int:
    """Run all isolation checks for ``main`` or ``lab``."""
    expect_lab = instance == "lab"
    expected_backend = BACKEND.resolve()

    checks: list[tuple[str, tuple[bool, str]]] = [
        ("Code root", verify_code_root(expected_backend=expected_backend)),
        ("LAB_MODE", verify_lab_mode(expect_lab=expect_lab)),
        ("Virtualenv", verify_venv_isolation(expect_lab=expect_lab)),
        ("Database", verify_database_isolation(expect_lab=expect_lab)),
        ("UI port", verify_ui_port(expected_port=expected_port)),
    ]

    failures: list[str] = []
    print()
    print(f"Instance isolation ({instance} / port {expected_port})")
    print("-" * 60)
    for label, (ok, detail) in checks:
        mark = "OK" if ok else "FAIL"
        print(f"  [{mark}] {label}: {detail}")
        if not ok:
            failures.append(f"{label}: {detail}")

    if failures:
        print()
        print("Isolation guard failed:")
        for item in failures:
            print(f"  - {item}")
        if expect_lab:
            print()
            print("  Fix lab: python scripts/lab/create_backup.py --skip-db")
            print("  Then:    cd trading-lab/backend && .venv/bin/pip install -e .")
        else:
            print()
            print("  Fix main: cd backend && .venv/bin/pip install -e .")
            print("  Ensure backend/.env has no LAB_MODE / LAB_SCHEMA")
        return 1 if strict else 0

    _ = load_database_url()  # validate .env readable
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify main/lab instance isolation")
    parser.add_argument("--instance", choices=("main", "lab"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()
    raise SystemExit(
        run_instance_guard(
            instance=args.instance,
            expected_port=args.port,
            strict=not args.warn_only,
        )
    )
