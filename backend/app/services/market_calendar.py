"""NSE session calendar helpers for EOD market-data sync."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
# Treat the session as in-progress until 4:30 PM IST (allows last-moment orders to settle).
NSE_EOD_CUTOFF = time(16, 30)
NSE_SESSION_OPEN = time(9, 15)
# Force-exit open bracket positions at or after this time (intraday square-off rule).
NSE_SQUARE_OFF = time(15, 25)
# Missed-profitable / NIFTY250 EOD analysis runs after this time (session effectively done for review).
NSE_MISSED_PROFITABLE_CUTOFF = time(15, 45)
# Mid-day recommendation re-analysis available from this time through session end.
NSE_MIDDAY_ANALYSIS_START = time(11, 45)

_HOLIDAYS_PATH = Path(__file__).resolve().parent.parent / "data" / "nse_trading_holidays.json"


@lru_cache(maxsize=1)
def _nse_holiday_dates() -> frozenset[date]:
    if not _HOLIDAYS_PATH.exists():
        return frozenset()
    try:
        payload = json.loads(_HOLIDAYS_PATH.read_text(encoding="utf-8"))
        raw = payload.get("holidays", payload if isinstance(payload, list) else [])
        return frozenset(date.fromisoformat(str(item)) for item in raw)
    except Exception:
        return frozenset()


def is_nse_holiday(d: date) -> bool:
    return d in _nse_holiday_dates()


def is_trading_day(d: date) -> bool:
    """Weekday that is not an NSE equity holiday."""
    return d.weekday() < 5 and not is_nse_holiday(d)


def get_previous_trading_day(d: date) -> date:
    """Previous NSE trading day (skips weekends and listed holidays)."""
    probe = d - timedelta(days=1)
    while not is_trading_day(probe):
        probe -= timedelta(days=1)
    return probe


def get_next_trading_day(d: date) -> date:
    """Next NSE trading day after d (skips weekends and listed holidays)."""
    probe = d + timedelta(days=1)
    while not is_trading_day(probe):
        probe += timedelta(days=1)
    return probe


def recommendation_prediction_date(
    data_through: date,
    *,
    now: datetime | None = None,
) -> date:
    """
    Target session for recommendation picks.

    - Before 4:30 PM on a trading day: predict for today's session when OHLC ends
      yesterday; if OHLC already includes today, predict for today.
    - After 4:30 PM (or on weekends/holidays): predict for the next trading session
      after the latest completed data.
    """
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()

    if is_trading_day(today) and is_trading_day_complete(today, now=current):
        base = max(data_through, today)
        return get_next_trading_day(base)

    if not is_trading_day(today):
        last_done = last_completed_trading_day(now=current)
        base = max(data_through, last_done)
        return get_next_trading_day(base)

    # Intraday — active session is today when we already have today's candle.
    if data_through >= today:
        return today
    return get_next_trading_day(data_through)


def is_live_quote_session(*, now: datetime | None = None) -> bool:
    """True during NSE cash hours when live LTP quotes are meaningful (9:15 AM – 4:30 PM IST)."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()
    if not is_trading_day(today):
        return False
    t = current.time()
    return NSE_SESSION_OPEN <= t < NSE_EOD_CUTOFF


def is_square_off_window(*, now: datetime | None = None) -> bool:
    """True from 3:25 PM IST through session end on a trading day — open positions must be closed."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()
    if not is_trading_day(today):
        return False
    t = current.time()
    return NSE_SQUARE_OFF <= t < NSE_EOD_CUTOFF


def is_missed_profitable_analysis_ready(trade_date: date, *, now: datetime | None = None) -> bool:
    """True when post-session EOD analysis may run for trade_date (after 3:45 PM IST on that day)."""
    return is_post_session_eod_ready(trade_date, now=now)


def is_post_session_eod_ready(trade_date: date, *, now: datetime | None = None) -> bool:
    """True when post-session EOD analysis may run for trade_date (after 3:45 PM IST on that day)."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    if not is_trading_day(trade_date):
        return True
    if trade_date < current.date():
        return True
    if trade_date > current.date():
        return False
    return current.time() >= NSE_MISSED_PROFITABLE_CUTOFF


def current_session_date(*, now: datetime | None = None) -> date:
    """IST calendar date for Orders/Trades tabs (show only today's activity)."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)
    return current.date()


def active_market_session_date(*, now: datetime | None = None) -> date:
    """Trade date for NIFTY250 day moves and recommendation matching."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()
    if not is_trading_day(today):
        return last_completed_trading_day(now=current)
    if current.time() >= NSE_EOD_CUTOFF:
        return today
    return today


def is_trading_day_complete(trade_date: date, *, now: datetime | None = None) -> bool:
    """True when the given trade date's session has finished (after 4:30 PM IST)."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    if not is_trading_day(trade_date):
        return True
    if trade_date < current.date():
        return True
    if trade_date > current.date():
        return False
    return current.time() >= NSE_EOD_CUTOFF


def closed_in_square_off_window(closed_at: datetime, trade_date: date) -> bool:
    """True when a plan was closed during the 3:25 PM IST square-off window on trade_date."""
    if closed_at.tzinfo is None:
        closed_at = closed_at.replace(tzinfo=IST)
    else:
        closed_at = closed_at.astimezone(IST)
    if closed_at.date() != trade_date:
        return False
    t = closed_at.time()
    return NSE_SQUARE_OFF <= t < NSE_EOD_CUTOFF


def is_midday_analysis_ready(*, now: datetime | None = None) -> bool:
    """True from 11:45 AM IST through session end on a trading day."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()
    if not is_trading_day(today):
        return False
    t = current.time()
    return NSE_MIDDAY_ANALYSIS_START <= t < NSE_EOD_CUTOFF


def market_data_sync_end_date(*, now: datetime | None = None) -> date:
    """
    Latest trade date to pull during market sync.

    After 3:45 PM IST on a trading day, include today so post-session analysis
    (peak vs exit, missed movers) has intraday OHLC. Before that, use the prior session.
    """
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()
    if not is_trading_day(today):
        return last_completed_trading_day(now=current)
    if current.time() >= NSE_MISSED_PROFITABLE_CUTOFF:
        return today
    return get_previous_trading_day(today)


def last_completed_trading_day(*, now: datetime | None = None) -> date:
    """
    Return the latest trade date that should be synced.

    During an open session (before 4:30 PM IST on a weekday), today's EOD data
    is not final yet — use the previous trading day. After the cutoff, include today.
    On weekends/holidays, use the prior session.
    """
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    today = current.date()
    if not is_trading_day(today):
        return get_previous_trading_day(today)
    if current.time() < NSE_EOD_CUTOFF:
        return get_previous_trading_day(today)
    return today
