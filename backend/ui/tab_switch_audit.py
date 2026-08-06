"""Audit timing for Streamlit sidebar navigation and page renders."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

PAGE_SLUGS: dict[str, str] = {
    "Trading": "trading",
    "Paper trading trend": "paper_trading_trend",
    "Pattern backtest": "pattern_backtest",
    "Recommendations": "recommendations",
    "Mid day recommendation analysis": "midday_recommendations",
    "Analysis & EOD": "analysis_eod",
    "Pattern definitions": "pattern_definitions",
}


def page_slug(page: str) -> str:
    return PAGE_SLUGS.get(page, page.lower().replace(" ", "_"))


def _record_render_audit(
    *,
    action: str,
    page: str,
    duration_ms: int,
    body_ms: int,
    total_ms: int,
    db_ready: bool,
    from_page: str | None = None,
    error: BaseException | None = None,
) -> None:
    try:
        from app.services.audit import record_audit_sync
        from app.services.audit_types import AuditComponent, AuditStatus
    except ImportError:
        return

    ctx: dict[str, object] = {
        "page": page,
        "page_slug": page_slug(page),
        "body_ms": body_ms,
        "total_ms": total_ms,
        "db_ready": db_ready,
    }
    if from_page:
        ctx["from_page"] = from_page
        ctx["from_page_slug"] = page_slug(from_page)

    status = AuditStatus.FAILED if error else AuditStatus.SUCCESS
    message = f"{action} {page} in {duration_ms}ms"
    if from_page and action == "ui.tab_switch":
        message = f"Tab switch {from_page} → {page} in {duration_ms}ms (body {body_ms}ms, total {total_ms}ms)"

    record_audit_sync(
        action=action,
        component=AuditComponent.UI.value,
        status=status,
        duration_ms=duration_ms,
        message=message,
        error=error,
        context=ctx,
    )


@contextmanager
def audit_page_render(
    page: str,
    *,
    from_page: str | None,
    db_ready: bool,
    main_start: float,
) -> Iterator[None]:
    """
    Time page body render and emit audit events.

    - ``ui.page_render`` on every rerun (same tab or switch).
    - ``ui.tab_switch`` additionally when ``from_page`` differs from ``page``.
    """
    body_start = time.perf_counter()
    err: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        from streamlit.runtime.scriptrunner_utils.exceptions import (
            RerunException,
            StopException,
        )

        if isinstance(exc, (RerunException, StopException)):
            raise
        err = exc
        raise
    finally:
        body_ms = int((time.perf_counter() - body_start) * 1000)
        total_ms = int((time.perf_counter() - main_start) * 1000)
        switched = from_page is not None and from_page != page

        _record_render_audit(
            action="ui.page_render",
            page=page,
            duration_ms=body_ms,
            body_ms=body_ms,
            total_ms=total_ms,
            db_ready=db_ready,
            error=err,
        )
        if switched:
            _record_render_audit(
                action="ui.tab_switch",
                page=page,
                duration_ms=total_ms,
                body_ms=body_ms,
                total_ms=total_ms,
                db_ready=db_ready,
                from_page=from_page,
                error=err,
            )
