"""UI load harness — simulate Streamlit navigation and assert pages render.

Used by integration / post-deploy tests to catch blank-page regressions without
dropping any tabs or features.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Sidebar Navigate radio labels — must stay in sync with ui.dashboard.main().
NAV_PAGES: tuple[str, ...] = (
    "Trading",
    "Paper trading trend",
    "Pattern backtest",
    "Recommendations",
    "Mid day recommendation analysis",
    "Analysis & EOD",
    "Pattern definitions",
)

PAGE_RENDERERS: tuple[str, ...] = (
    "render_trading_page",
    "render_backtest_page",
    "render_recommendations_page",
    "render_midday_recommendations_page",
    "render_eod_analysis_page",
    "render_paper_trading_trend_page",
    "main",
)

DASHBOARD_PATH = Path(__file__).resolve().parent / "dashboard.py"
APP_SHELL_MARKER = "trading-app-shell-ready"


@dataclass(frozen=True)
class PageLoadResult:
    page: str
    ok: bool
    title: str | None
    error: str | None
    element_count: int


def expected_title_for(page: str) -> str:
    titles = {
        "Trading": "NIFTY Paper Trading",
        "Paper trading trend": "Paper Trading Trend",
        "Pattern backtest": "Pattern Backtesting",
        "Recommendations": "Recommendation Engine",
        "Mid day recommendation analysis": "Mid Day Recommendation Analysis",
        "Analysis & EOD": "Analysis & EOD Report",
        "Pattern definitions": "Pattern Definitions",
    }
    return titles.get(page, "NIFTY Paper Trading")


def create_app_test(*, default_timeout: float = 90.0):
    """Build a Streamlit AppTest pointed at the dashboard entrypoint."""
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(DASHBOARD_PATH), default_timeout=default_timeout)


def _title_text(at: Any) -> str | None:
    try:
        titles = list(at.title)
    except Exception:
        return None
    if not titles:
        return None
    value = getattr(titles[0], "value", None)
    return str(value) if value is not None else None


def _exception_text(at: Any) -> str | None:
    exc = getattr(at, "exception", None)
    if not exc:
        return None
    return str(exc)


def _element_count(at: Any) -> int:
    total = 0
    for name in (
        "title",
        "markdown",
        "caption",
        "header",
        "subheader",
        "dataframe",
        "button",
        "radio",
        "selectbox",
        "metric",
        "info",
        "warning",
        "error",
        "expander",
    ):
        try:
            total += len(list(getattr(at, name)))
        except Exception:
            continue
    return total


def run_initial_load(at: Any | None = None, *, timeout: float = 90.0) -> PageLoadResult:
    """Load the default page (Trading) and verify the shell painted."""
    app = at or create_app_test(default_timeout=timeout)
    app.run(timeout=timeout)
    err = _exception_text(app)
    title = _title_text(app)
    count = _element_count(app)
    ok = err is None and count > 0 and (title is not None or count >= 3)
    return PageLoadResult(
        page="Trading",
        ok=ok,
        title=title,
        error=err,
        element_count=count,
    )


def navigate_and_load(at: Any, page: str, *, timeout: float = 120.0) -> PageLoadResult:
    """Select a Navigate page (via session state) and assert it rendered content."""
    try:
        at.session_state["nav_page"] = page
    except Exception as exc:
        return PageLoadResult(
            page=page,
            ok=False,
            title=None,
            error=f"Could not set nav_page session state: {exc}",
            element_count=0,
        )
    at.run(timeout=timeout)
    err = _exception_text(at)
    title = _title_text(at)
    count = _element_count(at)
    expected = expected_title_for(page)
    title_ok = (
        title is None
        or expected.lower() in (title or "").lower()
        or any(part.lower() in (title or "").lower() for part in page.split()[:2])
    )
    ok = err is None and count > 0 and title_ok
    return PageLoadResult(
        page=page,
        ok=ok,
        title=title,
        error=err,
        element_count=count,
    )


def load_all_nav_pages(*, timeout_per_page: float = 120.0) -> list[PageLoadResult]:
    """Simulate visiting every sidebar page once; return per-page results."""
    results: list[PageLoadResult] = []
    # Fresh AppTest per page avoids stale ElementTree sidebar KeyErrors across reruns.
    for page in NAV_PAGES:
        at = create_app_test(default_timeout=timeout_per_page)
        if page == "Trading":
            results.append(run_initial_load(at, timeout=timeout_per_page))
        else:
            # Prime the script once so session_state exists, then navigate.
            prime = run_initial_load(at, timeout=timeout_per_page)
            if not prime.ok:
                results.append(
                    PageLoadResult(
                        page=page,
                        ok=False,
                        title=prime.title,
                        error=f"prime load failed before navigate: {prime.error}",
                        element_count=prime.element_count,
                    )
                )
                continue
            results.append(navigate_and_load(at, page, timeout=timeout_per_page))
    return results


def assert_all_pages_loaded(results: list[PageLoadResult]) -> None:
    failures = [r for r in results if not r.ok]
    if failures:
        detail = "; ".join(
            f"{r.page}: error={r.error!r} title={r.title!r} elements={r.element_count}"
            for r in failures
        )
        raise AssertionError(f"UI page load failures: {detail}")
