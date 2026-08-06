"""Background live-quote prefetch — UI reads cache instantly, refresh runs off-thread."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
LIVE_POLL_INTERVAL_SEC = 10


@dataclass
class _LiveQuoteCache:
    quotes: dict[str, dict[str, float | None]] = field(default_factory=dict)
    fetched_at: datetime | None = None
    stats: dict[str, int] = field(default_factory=dict)
    notice: str | None = None
    error: str | None = None
    symbols_key: tuple[str, ...] = ()
    version: int = 0


_cache = _LiveQuoteCache()
_lock = threading.Lock()
_fetch_in_progress = False
_last_completed_mono: float = 0.0


def _symbols_key(symbols: list[str]) -> tuple[str, ...]:
    return tuple(sorted({s.upper() for s in symbols if s}))


def _notice_from_stats(stats: dict[str, int]) -> str | None:
    parts = [f"{k.replace('_', ' ')}: {v}" for k, v in stats.items() if v]
    if not parts:
        return None
    return "Bracket orders updated — " + ", ".join(parts)


def publish_refresh_result(
    quotes: dict[str, dict[str, float | None]] | dict[str, float],
    stats: dict[str, int] | None = None,
    *,
    symbols: list[str] | None = None,
    error: str | None = None,
) -> None:
    """Update module cache (main thread or worker)."""
    global _last_completed_mono
    notice = _notice_from_stats(stats or {})
    normalized: dict[str, dict[str, float | None]] = {}
    for sym, raw in (quotes or {}).items():
        if isinstance(raw, dict):
            normalized[sym] = raw
        else:
            normalized[sym] = {"ltp": float(raw), "open": None, "prev_close": None, "high": None}
    with _lock:
        if normalized:
            _cache.quotes = normalized
            _cache.fetched_at = datetime.now(IST)
        if symbols is not None:
            _cache.symbols_key = _symbols_key(symbols)
        _cache.stats = dict(stats or {})
        _cache.notice = notice
        _cache.error = error
        _cache.version += 1
        _last_completed_mono = time.monotonic()
        if normalized and error is None:
            from app.services.bracket_reconcile_state import record_live_poll

            record_live_poll(now=_cache.fetched_at)


def _fetch_worker(symbols: list[str]) -> None:
    global _fetch_in_progress
    try:
        from ui.async_runner import run_async
        from ui.streamlit_imports import ensure_live_quotes_fresh

        ensure_live_quotes_fresh()
        from ui.helpers import _refresh_live_trading

        quotes, stats = run_async(_refresh_live_trading(symbols))
        publish_refresh_result(
            quotes if quotes else {},
            stats,
            symbols=symbols,
        )
    except Exception as exc:
        publish_refresh_result({}, None, symbols=symbols, error=str(exc))
    finally:
        with _lock:
            _fetch_in_progress = False


def maybe_start_background_refresh(symbols: list[str], *, force: bool = False) -> None:
    """Start a background fetch when the interval elapsed and none is running."""
    global _fetch_in_progress
    if not symbols:
        return

    key = _symbols_key(symbols)
    now = time.monotonic()

    with _lock:
        if _fetch_in_progress:
            return
        symbols_changed = key != _cache.symbols_key
        interval_elapsed = (now - _last_completed_mono) >= LIVE_POLL_INTERVAL_SEC
        if not force and not symbols_changed and not interval_elapsed:
            return
        _fetch_in_progress = True

    threading.Thread(
        target=_fetch_worker,
        args=(list(symbols),),
        name="live-quote-poll",
        daemon=True,
    ).start()


def sync_cache_to_session(session_state: Any) -> None:
    """Apply the latest module cache to Streamlit session_state."""
    with _lock:
        quotes = dict(_cache.quotes)
        fetched_at = _cache.fetched_at
        error = _cache.error
        notice = _cache.notice
        version = _cache.version
        if notice:
            _cache.notice = None

    if quotes:
        session_state["position_live_quotes"] = quotes
    if fetched_at is not None:
        session_state["position_live_quotes_at"] = fetched_at
    if error:
        session_state["live_poll_error"] = error
    elif session_state.get("live_poll_error"):
        session_state.pop("live_poll_error", None)
    if notice:
        session_state["trade_plan_live_notice"] = notice
    session_state["_live_quote_cache_version"] = version


def reset_live_quote_cache() -> None:
    global _fetch_in_progress, _last_completed_mono
    with _lock:
        _cache.quotes.clear()
        _cache.fetched_at = None
        _cache.stats.clear()
        _cache.notice = None
        _cache.error = None
        _cache.symbols_key = ()
        _cache.version += 1
        _fetch_in_progress = False
        _last_completed_mono = 0.0
