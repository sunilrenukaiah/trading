"""Mid-day session OHLC sync — freshness skip, parallel fetch, ETA messaging."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Instrument, OhlcvCandle
from app.services.midday_market_sync import (
    SESSION_OHLC_MAX_CONCURRENT,
    format_eta,
    session_ohlc_progress_message,
    upsert_intraday_session_candles,
)


@pytest.mark.quick
def test_format_eta_seconds_and_minutes() -> None:
    assert format_eta(45) == "~45s left"
    assert format_eta(60) == "~1m left"
    assert format_eta(125) == "~2m 5s left"
    assert format_eta(0) == ""
    assert format_eta(None) == ""


@pytest.mark.quick
def test_session_ohlc_progress_message_includes_eta_and_skips() -> None:
    msg = session_ohlc_progress_message(
        symbol="RELIANCE",
        completed=12,
        total=180,
        eta_sec=135,
        fresh_skipped=70,
    )
    assert "RELIANCE" in msg
    assert "(12/180)" in msg
    assert "~2m 15s left" in msg
    assert "70 fresh skipped" in msg


@pytest.mark.quick
@pytest.mark.asyncio
async def test_upsert_intraday_skips_fresh_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    trade_date = date(2026, 8, 5)
    fresh_inst = Instrument(id=1, symbol="RELIANCE", name="Reliance")
    stale_inst = Instrument(id=2, symbol="TCS", name="TCS")
    fresh_candle = OhlcvCandle(
        instrument_id=1,
        trade_date=trade_date,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("104"),
        synced_at=datetime.now(timezone.utc),
    )

    session = AsyncMock()
    session.commit = AsyncMock()

    instrument_result = MagicMock()
    instrument_result.all.return_value = [fresh_inst, stale_inst]
    fresh_result = MagicMock()
    fresh_result.all.return_value = [fresh_candle]
    session.scalars = AsyncMock(side_effect=[instrument_result, fresh_result])

    monkeypatch.setattr(
        "app.services.midday_market_sync.is_midday_analysis_ready",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.midday_market_sync.current_session_date",
        lambda: trade_date,
    )
    monkeypatch.setattr(
        "app.services.midday_market_sync.market_data_sync_symbols",
        AsyncMock(return_value=["RELIANCE", "TCS"]),
    )

    fetch_calls: list[str] = []

    async def fake_parallel(
        symbols,
        *,
        max_concurrent,
        post_fetch_delay_sec,
        progress_callback,
        fresh_skipped,
    ):
        fetch_calls.extend(symbols)
        if progress_callback:
            progress_callback(
                len(symbols),
                len(symbols),
                session_ohlc_progress_message(
                    symbol=symbols[-1],
                    completed=len(symbols),
                    total=len(symbols),
                    eta_sec=0,
                    fresh_skipped=fresh_skipped,
                ),
            )
        return {
            sym: {
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "last": 104.0,
            }
            for sym in symbols
        }

    monkeypatch.setattr(
        "app.services.midday_market_sync._fetch_session_quotes_parallel",
        fake_parallel,
    )
    monkeypatch.setattr(
        "app.services.midday_market_sync.upsert_candles",
        AsyncMock(return_value=1),
    )

    progress: list[tuple] = []

    stats = await upsert_intraday_session_candles(
        session,
        progress_callback=lambda i, total, msg: progress.append((i, total, msg)),
    )

    assert fetch_calls == ["TCS"]
    assert stats["symbols_fresh_skipped"] == 1
    assert stats["candles_upserted"] == 1
    assert stats["symbols_fetched"] == 1
    assert progress
    assert "fresh skipped" in progress[-1][2]


@pytest.mark.quick
@pytest.mark.asyncio
async def test_fetch_session_quotes_parallel_respects_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import midday_market_sync as mod

    active = 0
    peak = 0
    lock = asyncio.Lock()

    def fake_fetch(symbol: str) -> dict:
        return {
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "last": 1.5,
            "source": "test",
        }

    async def slow_to_thread(fn, symbol):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        try:
            return fn(symbol)
        finally:
            async with lock:
                active -= 1

    monkeypatch.setattr(mod.asyncio, "to_thread", slow_to_thread)
    monkeypatch.setattr(mod, "fetch_session_ohlc_sync", fake_fetch)
    monkeypatch.setattr(mod, "SESSION_OHLC_POST_FETCH_DELAY_SEC", 0.0)

    symbols = [f"SYM{i}" for i in range(12)]
    quotes = await mod._fetch_session_quotes_parallel(
        symbols,
        max_concurrent=3,
        post_fetch_delay_sec=0.0,
        progress_callback=None,
        fresh_skipped=0,
    )

    assert set(quotes) == set(symbols)
    assert peak <= 3
    assert peak >= 2


@pytest.mark.quick
@pytest.mark.asyncio
async def test_fetch_session_quotes_parallel_reports_eta(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import midday_market_sync as mod

    monkeypatch.setattr(
        mod,
        "fetch_session_ohlc_sync",
        lambda symbol: {
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "last": 1.5,
        },
    )
    monkeypatch.setattr(mod, "SESSION_OHLC_POST_FETCH_DELAY_SEC", 0.0)

    messages: list[str] = []

    await mod._fetch_session_quotes_parallel(
        ["AAA", "BBB"],
        max_concurrent=SESSION_OHLC_MAX_CONCURRENT,
        post_fetch_delay_sec=0.0,
        progress_callback=lambda _i, _t, msg: messages.append(msg),
        fresh_skipped=5,
    )

    assert messages
    assert "fresh skipped" in messages[0]
    assert "left" in messages[0]
    assert messages[-1].startswith("Session OHLC · BBB")
