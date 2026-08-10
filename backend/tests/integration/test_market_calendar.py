"""NSE session calendar tests."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.market_calendar import (
    IST,
    is_live_quote_session,
    is_missed_profitable_analysis_ready,
    is_post_session_eod_ready,
    is_square_off_window,
    last_completed_trading_day,
    market_data_sync_end_date,
)

IST_DT = lambda y, m, d, h, mi=0: datetime(y, m, d, h, mi, tzinfo=IST)


@pytest.mark.quick
def test_during_session_uses_previous_trading_day() -> None:
    # Wed 30 Jul 2026 10:30 IST — Jul 30 session still open
    now = IST_DT(2026, 7, 30, 10, 30)
    assert last_completed_trading_day(now=now) == date(2026, 7, 29)


@pytest.mark.quick
def test_after_cutoff_uses_same_day() -> None:
    now = IST_DT(2026, 7, 30, 16, 30)
    assert last_completed_trading_day(now=now) == date(2026, 7, 30)


@pytest.mark.quick
def test_before_cutoff_after_regular_close_uses_previous_day() -> None:
    # 4:00 PM — regular close done but cutoff not reached yet
    now = IST_DT(2026, 7, 30, 16, 0)
    assert last_completed_trading_day(now=now) == date(2026, 7, 29)


@pytest.mark.quick
def test_before_open_uses_previous_trading_day() -> None:
    now = IST_DT(2026, 7, 30, 9, 0)
    assert last_completed_trading_day(now=now) == date(2026, 7, 29)


@pytest.mark.quick
def test_weekend_uses_last_friday() -> None:
    # Sat 1 Aug 2026
    now = IST_DT(2026, 8, 1, 11, 0)
    assert last_completed_trading_day(now=now) == date(2026, 7, 31)


@pytest.mark.quick
def test_live_quote_session_before_open() -> None:
    now = IST_DT(2026, 7, 30, 9, 0)
    assert is_live_quote_session(now=now) is False


@pytest.mark.quick
def test_square_off_window_starts_at_325pm() -> None:
    assert is_square_off_window(now=IST_DT(2026, 7, 30, 15, 24)) is False
    assert is_square_off_window(now=IST_DT(2026, 7, 30, 15, 25)) is True
    assert is_square_off_window(now=IST_DT(2026, 7, 30, 16, 0)) is True


@pytest.mark.quick
def test_square_off_not_on_weekend() -> None:
    assert is_square_off_window(now=IST_DT(2026, 8, 1, 15, 30)) is False


@pytest.mark.quick
def test_missed_profitable_analysis_waits_until_345pm() -> None:
    trade_date = date(2026, 7, 30)
    assert is_post_session_eod_ready(trade_date, now=IST_DT(2026, 7, 30, 15, 44)) is False
    assert is_post_session_eod_ready(trade_date, now=IST_DT(2026, 7, 30, 15, 45)) is True
    assert is_post_session_eod_ready(trade_date, now=IST_DT(2026, 7, 30, 16, 0)) is True
    assert is_missed_profitable_analysis_ready(trade_date, now=IST_DT(2026, 7, 30, 15, 45)) is True


@pytest.mark.quick
def test_missed_profitable_analysis_ready_for_past_dates() -> None:
    assert is_post_session_eod_ready(date(2026, 7, 29), now=IST_DT(2026, 7, 30, 10, 0)) is True


@pytest.mark.quick
def test_market_data_sync_end_date_includes_today_after_345pm() -> None:
    assert market_data_sync_end_date(now=IST_DT(2026, 7, 30, 15, 44)) == date(2026, 7, 29)
    assert market_data_sync_end_date(now=IST_DT(2026, 7, 30, 15, 45)) == date(2026, 7, 30)
    assert market_data_sync_end_date(now=IST_DT(2026, 7, 30, 16, 0)) == date(2026, 7, 30)


@pytest.mark.quick
def test_market_data_date_range_ends_at_sync_end_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    from app.services import ingestion

    fixed_end = date(2026, 7, 30)
    monkeypatch.setattr(ingestion, "market_data_sync_end_date", lambda: fixed_end)

    start, end = ingestion.market_data_date_range(backfill_days=90)
    assert end == fixed_end
    assert start == fixed_end - timedelta(days=ingestion.effective_backfill_days(90))


@pytest.mark.quick
def test_get_next_trading_day_skips_weekend() -> None:
    from app.services.market_calendar import get_next_trading_day

    # Fri 31 Jul 2026 -> Mon 3 Aug 2026
    assert get_next_trading_day(date(2026, 7, 31)) == date(2026, 8, 3)


@pytest.mark.quick
def test_get_next_trading_day_skips_nse_holiday() -> None:
    from app.services.market_calendar import get_next_trading_day

    # Thu 1 Oct 2026 -> Mon 5 Oct (Gandhi Jayanti Fri 2 Oct is a holiday)
    assert get_next_trading_day(date(2026, 10, 1)) == date(2026, 10, 5)


@pytest.mark.quick
def test_recommendation_prediction_after_session_close() -> None:
    from app.services.market_calendar import recommendation_prediction_date

    now = IST_DT(2026, 7, 30, 17, 0)
    assert recommendation_prediction_date(date(2026, 7, 30), now=now) == date(2026, 7, 31)


@pytest.mark.quick
def test_recommendation_prediction_intraday_uses_today() -> None:
    from app.services.market_calendar import recommendation_prediction_date

    now = IST_DT(2026, 7, 30, 11, 0)
    assert recommendation_prediction_date(date(2026, 7, 29), now=now) == date(2026, 7, 30)


@pytest.mark.quick
def test_recommendation_prediction_stale_sync_after_close() -> None:
    """After 4:30 PM, stale data_through still targets next session."""
    from app.services.market_calendar import recommendation_prediction_date

    now = IST_DT(2026, 7, 30, 17, 30)
    assert recommendation_prediction_date(date(2026, 7, 29), now=now) == date(2026, 7, 31)


@pytest.mark.quick
def test_recommendation_prediction_on_weekend() -> None:
    from app.services.market_calendar import recommendation_prediction_date

    now = IST_DT(2026, 8, 1, 12, 0)  # Saturday
    assert recommendation_prediction_date(date(2026, 7, 31), now=now) == date(2026, 8, 3)


@pytest.mark.quick
def test_evening_recommendation_ready_after_6pm() -> None:
    from app.services.market_calendar import is_evening_recommendation_ready

    assert is_evening_recommendation_ready(now=IST_DT(2026, 7, 30, 17, 59)) is False
    assert is_evening_recommendation_ready(now=IST_DT(2026, 7, 30, 18, 0)) is True
    assert is_evening_recommendation_ready(now=IST_DT(2026, 8, 1, 10, 0)) is True  # Saturday
