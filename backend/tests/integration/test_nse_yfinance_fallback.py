"""NSE provider access-failure detection and sticky yfinance fallback."""

from __future__ import annotations

import pytest

from app.providers.nse_provider import (
    _is_nse_access_failure,
    _activate_yfinance_fallback,
    reset_nse_yfinance_fallback_for_tests,
    _NSE_FORCE_YFINANCE,
)


@pytest.mark.quick
def test_nse_access_failure_detects_403_session() -> None:
    reset_nse_yfinance_fallback_for_tests()
    assert _is_nse_access_failure(
        RuntimeError("Failed to establish session with NSE: 403 Client Error: Forbidden")
    )
    assert _is_nse_access_failure(Exception("403 Forbidden for url: https://www.nseindia.com/"))
    assert not _is_nse_access_failure(ValueError("symbol not found"))


@pytest.mark.quick
def test_nse_connection_error_counts_as_access_failure() -> None:
    from nsefeed.exceptions import NSEConnectionError

    reset_nse_yfinance_fallback_for_tests()
    assert _is_nse_access_failure(NSEConnectionError("session failed"))


@pytest.mark.quick
def test_activate_yfinance_fallback_is_sticky() -> None:
    reset_nse_yfinance_fallback_for_tests()
    from app.providers import nse_provider as mod

    assert mod._NSE_FORCE_YFINANCE is False
    _activate_yfinance_fallback("403 Forbidden")
    assert mod._NSE_FORCE_YFINANCE is True
    reset_nse_yfinance_fallback_for_tests()
