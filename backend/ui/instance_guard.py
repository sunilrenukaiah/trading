"""Runtime isolation — fail fast if main (8501) loads lab code or vice versa."""

from __future__ import annotations

import os
from pathlib import Path


def _lab_mode_on() -> bool:
    return os.environ.get("LAB_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def _path_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _backend_implies_lab(backend: Path) -> bool:
    return (backend.parent / ".lab-meta.json").exists()


def assert_instance_isolation() -> None:
    """Call at Streamlit startup; raises if code/env do not match this process."""
    backend = Path(__file__).resolve().parent.parent
    lab_on = _lab_mode_on()
    implies_lab = _backend_implies_lab(backend)

    import app.config  # noqa: WPS433
    import ui.helpers  # noqa: WPS433

    for label, module in (("app", app.config), ("ui", ui.helpers)):
        mod_root = Path(module.__file__).resolve().parent
        if label == "app":
            mod_root = mod_root.parent
        if not _path_under(mod_root, backend):
            raise RuntimeError(
                f"{label} loaded from {mod_root} but this dashboard lives under {backend}. "
                f"Run `pip install -e .` from {backend} (main and lab need separate venvs)."
            )

    if lab_on and not implies_lab:
        raise RuntimeError(
            "LAB_MODE=1 on the main instance — use scripts/lab/run_lab_app.py (port 8502)."
        )
    if not lab_on and implies_lab:
        raise RuntimeError(
            "Lab project tree without LAB_MODE=1 — check trading-lab/backend/.env."
        )

    if lab_on:
        schema = os.environ.get("LAB_SCHEMA", "").strip()
        if not schema or schema.lower() == "public":
            raise RuntimeError(
                "LAB_MODE=1 without LAB_SCHEMA — lab would read/write the main database schema."
            )
    elif os.environ.get("LAB_SCHEMA", "").strip():
        raise RuntimeError(
            "LAB_SCHEMA is set on the main instance — remove from backend/.env (port 8501)."
        )

    port_raw = os.environ.get("TRADING_UI_PORT", "").strip()
    if port_raw:
        expected = 8502 if lab_on else 8501
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise RuntimeError(f"TRADING_UI_PORT={port_raw!r} is not valid") from exc
        if port != expected:
            raise RuntimeError(
                f"TRADING_UI_PORT={port} conflicts with instance "
                f"({'lab' if lab_on else 'main'} expects {expected})"
            )


def instance_label() -> str:
    return "lab" if _lab_mode_on() else "main"
