"""Market data ingestion — universe reconciliation and cleanup."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.defaults import DEFAULT_MARKET_DATA_UNIVERSE
from app.models import Instrument, InstrumentType


@pytest.mark.quick
def test_market_data_universe_default() -> None:
    from app.services.ingestion import market_data_universe

    assert market_data_universe() == DEFAULT_MARKET_DATA_UNIVERSE


@pytest.mark.quick
def test_market_data_date_range_uses_backfill_days(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import ingestion

    fixed_end = date(2026, 7, 29)
    monkeypatch.setattr(ingestion, "market_data_sync_end_date", lambda: fixed_end)

    start, end = ingestion.market_data_date_range(backfill_days=90)
    assert end == fixed_end
    # Explicit 90 is expanded to cover the 51-trading-day simulation window (~98 calendar days).
    assert start == end - timedelta(days=ingestion.effective_backfill_days(90))
    assert (end - start).days >= 51


@pytest.mark.quick
def test_all_market_data_symbols_is_nifty_universe_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import ingestion

    monkeypatch.setattr(ingestion, "market_data_universe", lambda: "NIFTY250")
    monkeypatch.setattr(ingestion, "get_universe_symbols", lambda _u: ("AAA", "BBB"))

    assert ingestion._all_market_data_symbols() == ["AAA", "BBB"]


@pytest.mark.quick
def test_nifty50_helpers_load() -> None:
    from app.services.ingestion import _nifty50_name_map, _nifty50_symbols

    names = _nifty50_name_map()
    symbols = _nifty50_symbols()
    assert "RELIANCE" in symbols
    assert names.get("RELIANCE")


@pytest.mark.quick
@pytest.mark.asyncio
async def test_prune_candles_outside_range() -> None:
    from app.services.ingestion import prune_candles_outside_range

    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 5
    session.execute = AsyncMock(return_value=result)

    deleted = await prune_candles_outside_range(
        session,
        date(2026, 1, 1),
        date(2026, 3, 1),
    )
    assert deleted == 5
    session.execute.assert_awaited_once()


@pytest.mark.quick
@pytest.mark.asyncio
async def test_prune_instruments_not_in_universe_deletes_candles_and_instrument() -> None:
    from app.services.ingestion import prune_instruments_not_in_universe

    stale = Instrument(
        id=99,
        symbol="OLDCO",
        name="Old Co",
        exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
        yfinance_symbol="OLDCO.NS",
        is_nifty50=False,
        is_active=True,
    )
    keep = Instrument(
        id=1,
        symbol="RELIANCE",
        name="Reliance",
        exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
        yfinance_symbol="RELIANCE.NS",
        is_nifty50=True,
        is_active=True,
    )

    session = AsyncMock()
    scalar_calls = {"n": 0}

    async def _scalars(_stmt):
        scalar_calls["n"] += 1
        # 1st call: all instruments; later calls: paper-ref instrument ids (none)
        if scalar_calls["n"] == 1:
            return [keep, stale]
        return []

    session.scalars = _scalars

    candle_result = MagicMock(rowcount=120)
    session.execute = AsyncMock(return_value=candle_result)

    stats = await prune_instruments_not_in_universe(session, {"RELIANCE"})

    assert stats["candles_deleted"] == 120
    assert stats["instruments_deleted"] == 1
    assert stats["instruments_deactivated"] == 0
    assert session.execute.await_count == 2  # delete candles + delete instruments


@pytest.mark.quick
@pytest.mark.asyncio
async def test_prune_instruments_keeps_candles_for_open_positions() -> None:
    from app.services.ingestion import prune_instruments_not_in_universe

    held = Instrument(
        id=92,
        symbol="CDSL",
        name="CDSL",
        exchange="NSE",
        instrument_type=InstrumentType.EQUITY,
        yfinance_symbol="CDSL.NS",
        is_nifty50=False,
        is_active=True,
    )

    session = AsyncMock()
    scalar_calls = {"n": 0}

    async def _scalars(_stmt):
        scalar_calls["n"] += 1
        if scalar_calls["n"] == 1:
            return [held]
        # Paper refs: instrument id 92 is referenced
        return [92]

    session.scalars = _scalars
    session.execute = AsyncMock()

    stats = await prune_instruments_not_in_universe(session, {"RELIANCE"})

    assert stats["instruments_deactivated"] == 1
    assert stats["instruments_deleted"] == 0
    assert stats["candles_deleted"] == 0
    session.execute.assert_not_awaited()
    assert held.is_active is False


@pytest.mark.quick
def test_refresh_universe_symbols_updates_cache(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import nifty_universe as nu

    cache_file = tmp_path / "nifty_universe_cache.json"
    monkeypatch.setattr(nu, "CACHE_PATH", cache_file)
    monkeypatch.setattr(nu, "_fetch_nse_symbols", lambda _name: ["AAA", "BBB"])
    nu.get_universe_symbols.cache_clear()

    symbols = nu.refresh_universe_symbols("NIFTY250")

    assert symbols == ["AAA", "BBB"]
    assert nu.get_universe_symbols("NIFTY250") == ("AAA", "BBB")


@pytest.mark.quick
def test_missing_fetch_ranges() -> None:
    from app.services.ingestion import _missing_fetch_ranges

    end = date(2026, 7, 29)
    start = end - timedelta(days=90)
    assert _missing_fetch_ranges(window_start=start, end=end, latest=None) == [(start, end)]
    assert _missing_fetch_ranges(window_start=start, end=end, latest=end) == []
    assert _missing_fetch_ranges(window_start=start, end=end, latest=end - timedelta(days=3)) == [
        (end - timedelta(days=2), end)
    ]
    assert _missing_fetch_ranges(
        window_start=start,
        end=end,
        latest=end,
        earliest=date(2026, 6, 1),
    ) == [(start, date(2026, 5, 31))]


@pytest.mark.quick
def test_market_data_instruments_use_universe_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import ingestion

    monkeypatch.setattr(ingestion, "market_data_universe", lambda: "NIFTY250")
    monkeypatch.setattr(ingestion, "get_universe_symbols", lambda _u: ("AAA", "BBB"))
    monkeypatch.setattr(ingestion, "_open_position_symbols", AsyncMock(return_value=set()))

    async def _fake_ensure(_session):
        return 0

    monkeypatch.setattr(ingestion, "ensure_market_data_instruments", _fake_ensure)

    class _ScalarResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _FakeInstrument:
        def __init__(self, symbol: str):
            self.symbol = symbol
            self.id = hash(symbol) % 1000

    instruments = [_FakeInstrument("AAA"), _FakeInstrument("BBB")]

    class _FakeSession:
        async def scalars(self, _stmt):
            return _ScalarResult(instruments)

    async def _run():
        return await ingestion._market_data_instruments(_FakeSession())

    import asyncio

    rows = asyncio.run(_run())
    assert len(rows) == 2
    assert {r.symbol for r in rows} == {"AAA", "BBB"}
