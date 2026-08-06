"""Daily applicable-rates refresh at 9 AM IST or first app start of the day."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import streamlit as st

from app.services.applicable_rates import refresh_applicable_rates
from app.services.market_calendar import IST

RATES_REFRESH_AT = time(9, 0)
_SESSION_DAY_KEY = "applicable_rates_session_day"


def _now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        return current.replace(tzinfo=IST)
    return current.astimezone(IST)


def _already_refreshed_today(*, now: datetime | None = None) -> bool:
    from app.services.applicable_rates import get_applicable_rates

    current = _now(now)
    rates = get_applicable_rates()
    if rates.last_refreshed_date is None:
        return False
    return rates.last_refreshed_date >= current.date()


def _is_first_start_of_day(*, now: datetime | None = None) -> bool:
    """True on the first Streamlit session tick for this IST calendar day."""
    current = _now(now)
    day_key = current.date().isoformat()
    if st.session_state.get(_SESSION_DAY_KEY) == day_key:
        return False
    st.session_state[_SESSION_DAY_KEY] = day_key
    return True


def due_rates_refresh(*, now: datetime | None = None) -> bool:
    """
    True once per IST day when refresh is needed:

    - First app start of the day (any time), or
    - From 9:00 AM onward if the app stayed open overnight and rates are stale.
    """
    if _already_refreshed_today(now=now):
        return False

    current = _now(now)
    if _is_first_start_of_day(now=current):
        return True
    return current.time() >= RATES_REFRESH_AT


def try_refresh_applicable_rates() -> dict | None:
    """
    Refresh statutory rates when due. Returns summary dict or None if skipped.
    """
    if not due_rates_refresh():
        return None

    if st.session_state.get("_rates_refresh_inflight"):
        return None

    st.session_state["_rates_refresh_inflight"] = True
    try:
        rates = refresh_applicable_rates()
    finally:
        st.session_state.pop("_rates_refresh_inflight", None)

    return {
        "stcg_tax_rate": rates.stcg_tax_rate,
        "stt_rate": rates.stt_rate,
        "stamp_duty_rate": rates.stamp_duty_rate,
        "brokerage_rate": rates.brokerage_rate,
        "sources": rates.sources,
        "notes": rates.notes,
        "refreshed_at": (
            rates.last_refreshed_at.isoformat() if rates.last_refreshed_at else None
        ),
    }
