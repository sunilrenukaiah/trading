"""Durable daily market-sync status — skip auto-sync when today's data is already in.

After ~4:00 PM IST on a trading day, one successful sync that covers the required
trade date is enough for scheduled / automatic refreshes. Manual "Refresh market
data" always forces a sync.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from app.services.market_calendar import IST, market_data_sync_end_date

STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "market_sync_status.json"
# User rule: after 4 PM IST, reuse local data if that day's sync already ran.
DAILY_AUTO_SYNC_CUTOFF = time(16, 0)
# First scheduled post-session slot (3:45 PM IST).
NSE_POST_SESSION_SYNC_FLOOR = time(15, 45)


def _now_ist(now: datetime | None = None) -> datetime:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        return current.replace(tzinfo=IST)
    return current.astimezone(IST)


def load_market_sync_status() -> dict[str, Any]:
    if not STATUS_PATH.exists():
        return {}
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_market_sync_status(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def record_market_sync_success(
    data_through: date | str | None,
    *,
    now: datetime | None = None,
) -> None:
    """Persist that market data was synced through ``data_through``."""
    current = _now_ist(now)
    if data_through is None:
        through = market_data_sync_end_date(now=current)
    elif isinstance(data_through, str):
        through = date.fromisoformat(data_through[:10])
    else:
        through = data_through

    payload = load_market_sync_status()
    payload.update(
        {
            "synced_trade_date": through.isoformat(),
            "synced_at": current.isoformat(),
            "synced_on_calendar_date": current.date().isoformat(),
        }
    )
    save_market_sync_status(payload)


def daily_auto_sync_already_done(*, now: datetime | None = None) -> bool:
    """
    True when a sync for the required trade date already completed today
    (at/after the post-session window). Auto jobs should skip; manual refresh should not.
    """
    current = _now_ist(now)
    target = market_data_sync_end_date(now=current)
    payload = load_market_sync_status()
    synced_trade = payload.get("synced_trade_date")
    synced_at_raw = payload.get("synced_at")
    if not synced_trade or not synced_at_raw:
        return False
    try:
        synced_trade_date = date.fromisoformat(str(synced_trade)[:10])
        synced_at = datetime.fromisoformat(str(synced_at_raw))
    except ValueError:
        return False
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=IST)
    else:
        synced_at = synced_at.astimezone(IST)

    if synced_trade_date < target:
        return False
    # Must have been recorded on this calendar day in the post-session window.
    if synced_at.date() != current.date():
        return False
    if synced_at.time() < NSE_POST_SESSION_SYNC_FLOOR:
        return False
    return True


async def latest_ohlcv_trade_date() -> date | None:
    """Max trade_date in ohlcv_candles (None if empty / DB unavailable)."""
    from sqlalchemy import func, select

    from app.db.ui_session import ui_session
    from app.models import OhlcvCandle

    try:
        async with ui_session() as session:
            latest = await session.scalar(select(func.max(OhlcvCandle.trade_date)))
    except Exception:
        return None
    return latest


async def daily_auto_sync_needed(*, force: bool = False, now: datetime | None = None) -> bool:
    """Whether an automatic market sync should run."""
    if force:
        return True
    current = _now_ist(now)
    if daily_auto_sync_already_done(now=current):
        return False

    # DB fallback (status file missing after machine migrate): after 4 PM, if
    # candles already cover the required trade date, treat as done.
    if current.time() >= DAILY_AUTO_SYNC_CUTOFF:
        target = market_data_sync_end_date(now=current)
        latest = await latest_ohlcv_trade_date()
        if latest is not None and latest >= target:
            record_market_sync_success(latest, now=current)
            return False
    return True
