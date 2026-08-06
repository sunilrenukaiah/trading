import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ui_session import db_session
from app.db.session import AsyncSessionLocal
from app.config import settings
from app.defaults import DEFAULT_MARKET_DATA_UNIVERSE
from app.models import (
    Instrument,
    InstrumentType,
    OhlcvCandle,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    PaperTrade,
)
from app.providers import get_market_data_provider
from app.providers.base import CandleData
from app.services.app_logger import get_logger
from app.services.market_calendar import market_data_sync_end_date

log = get_logger(__name__)
from app.services.nifty_universe import ensure_universe_symbols_fresh, get_universe_symbols
from app.services.ohlcv_utils import valid_candle_prices
from app.services.paper_trading import PaperTradingService

NIFTY50_PATH = Path(__file__).resolve().parent.parent / "data" / "nifty50.json"


def _all_market_data_symbols() -> list[str]:
    """Symbols synced on refresh — configured NIFTY universe (default NIFTY250)."""
    universe = market_data_universe()
    return sorted(get_universe_symbols(universe))


async def _open_position_symbols(session: AsyncSession) -> set[str]:
    """Symbols with open paper positions — always keep market data for these."""
    account = await session.scalar(select(PaperAccount).limit(1))
    if account is None:
        return set()
    rows = (
        await session.scalars(
            select(Instrument.symbol)
            .join(PaperPosition, PaperPosition.instrument_id == Instrument.id)
            .where(PaperPosition.account_id == account.id, PaperPosition.quantity > 0)
        )
    ).all()
    return {s.upper() for s in rows}


async def market_data_sync_symbols(session: AsyncSession) -> list[str]:
    """NIFTY universe plus any symbol with an open position (e.g. legacy holdings)."""
    symbols = set(_all_market_data_symbols())
    symbols.update(await _open_position_symbols(session))
    return sorted(symbols)


def market_data_date_range(*, backfill_days: int | None = None) -> tuple[date, date]:
    end = market_data_sync_end_date()
    days = effective_backfill_days(backfill_days)
    start = end - timedelta(days=days)
    return start, end


def effective_backfill_days(explicit: int | None = None) -> int:
    """Calendar days to fetch — at least enough trading days for the 30-day simulation."""
    from app.defaults import DEFAULT_SIMULATION_UNIVERSE
    from app.services.backtest import required_backfill_calendar_days
    from app.services.nifty_universe import get_universe_config

    uni = getattr(settings, "default_simulation_universe", DEFAULT_SIMULATION_UNIVERSE)
    cfg = get_universe_config(uni)
    sim_required = required_backfill_calendar_days(cfg["lookback_days"], cfg["eval_days"])
    configured = explicit if explicit is not None else settings.backfill_days
    return max(configured, sim_required)


def market_data_universe() -> str:
    return getattr(settings, "market_data_universe", DEFAULT_MARKET_DATA_UNIVERSE).upper()


def provider_market_symbol(instrument: Instrument) -> str:
    """Symbol passed to the active market-data provider."""
    if settings.data_provider == "yfinance":
        return instrument.yfinance_symbol
    return instrument.symbol


def _nifty50_name_map() -> dict[str, str]:
    if not NIFTY50_PATH.exists():
        return {}
    data = json.loads(NIFTY50_PATH.read_text())
    names = {row["symbol"].upper(): row["name"] for row in data.get("constituents", [])}
    index = data.get("index")
    if index:
        names[index["symbol"].upper()] = index["name"]
    return names


def _nifty50_symbols() -> set[str]:
    if not NIFTY50_PATH.exists():
        return set()
    data = json.loads(NIFTY50_PATH.read_text())
    symbols = {row["symbol"].upper() for row in data.get("constituents", [])}
    index = data.get("index")
    if index:
        symbols.add(index["symbol"].upper())
    return symbols


async def ensure_market_data_instruments(session: AsyncSession) -> int:
    """Ensure NIFTY universe + open-position symbols exist for OHLCV storage."""
    symbols = await market_data_sync_symbols(session)
    name_map = _nifty50_name_map()
    nifty50 = _nifty50_symbols()
    added = 0

    for symbol in symbols:
        existing = await session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if existing:
            if existing.instrument_type == InstrumentType.EQUITY:
                existing.is_active = True
                existing.is_nifty50 = symbol in nifty50
            continue

        is_nifty50 = symbol in nifty50
        session.add(
            Instrument(
                symbol=symbol,
                name=name_map.get(symbol, symbol),
                exchange="NSE",
                instrument_type=InstrumentType.EQUITY,
                yfinance_symbol=f"{symbol}.NS",
                is_nifty50=is_nifty50,
                is_active=True,
            )
        )
        added += 1

    if added:
        await session.commit()
    return added


async def seed_instruments(session: AsyncSession) -> None:
    data = json.loads(NIFTY50_PATH.read_text())
    rows = [data["index"], *data["constituents"]]

    for row in rows:
        symbol = row["symbol"]
        yf_symbol = row.get("yfinance_symbol") or f"{symbol}.NS"
        instrument_type = InstrumentType(row.get("instrument_type", "EQUITY"))
        is_nifty50 = instrument_type == InstrumentType.EQUITY

        existing = await session.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if existing:
            continue

        session.add(
            Instrument(
                symbol=symbol,
                name=row["name"],
                exchange="NSE",
                instrument_type=instrument_type,
                yfinance_symbol=yf_symbol,
                is_nifty50=is_nifty50,
                is_active=True,
            )
        )
    await session.commit()
    # Do not call ensure_market_data_instruments here — it resolves NIFTY250 via NSE
    # and can hang for minutes on Streamlit Cloud (non-India IP / cold network).
    # Market sync / cloud jobs expand the universe when they run.


async def seed_paper_account(session: AsyncSession) -> None:
    existing = await session.scalar(select(PaperAccount).limit(1))
    if existing:
        return
    session.add(
        PaperAccount(
            name="Default",
            initial_cash=Decimal(str(settings.daily_trading_budget_inr)),
            cash_balance=Decimal(str(settings.daily_trading_budget_inr)),
        )
    )
    await session.commit()


async def _market_data_instruments(session: AsyncSession) -> list[Instrument]:
    symbols = await market_data_sync_symbols(session)
    if not symbols:
        return []

    await ensure_market_data_instruments(session)
    return list(
        (
            await session.scalars(
                select(Instrument)
                .where(Instrument.symbol.in_(symbols))
                .order_by(Instrument.symbol)
            )
        ).all()
    )


async def upsert_candles(session: AsyncSession, instrument_id: int, candles: list[CandleData]) -> int:
    count = 0
    for candle in candles:
        if not valid_candle_prices(candle.open, candle.high, candle.low, candle.close):
            continue
        stmt = insert(OhlcvCandle).values(
            instrument_id=instrument_id,
            trade_date=candle.trade_date,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            source=settings.data_provider,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "trade_date"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "source": stmt.excluded.source,
                "synced_at": func.now(),
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def upsert_symbol_candles(symbol: str, candles: list[CandleData]) -> int:
    """Persist NSE candles for one symbol using a short-lived session."""
    if not candles:
        return 0

    async with db_session() as session:
        await ensure_market_data_instruments(session)
        instrument = await session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        if not instrument:
            return 0
        count = await upsert_candles(session, instrument.id, candles)
        await session.commit()
        return count


async def _instrument_has_paper_refs(session: AsyncSession, instrument_id: int) -> bool:
    for model in (PaperOrder, PaperPosition, PaperTrade):
        ref = await session.scalar(
            select(model.id).where(model.instrument_id == instrument_id).limit(1)
        )
        if ref is not None:
            return True
    return False


async def prune_candles_outside_range(
    session: AsyncSession,
    start: date,
    end: date,
) -> int:
    """Delete OHLCV rows outside the allowed backfill window."""
    result = await session.execute(
        delete(OhlcvCandle).where(
            or_(OhlcvCandle.trade_date < start, OhlcvCandle.trade_date > end)
        )
    )
    return int(result.rowcount or 0)


async def prune_invalid_candles(session: AsyncSession) -> int:
    """Delete rows with NaN/invalid OHLCV (e.g. corrupt provider rows)."""
    result = await session.execute(
        delete(OhlcvCandle).where(
            or_(
                OhlcvCandle.open != OhlcvCandle.open,
                OhlcvCandle.high != OhlcvCandle.high,
                OhlcvCandle.low != OhlcvCandle.low,
                OhlcvCandle.close != OhlcvCandle.close,
                OhlcvCandle.open <= 0,
                OhlcvCandle.high <= 0,
                OhlcvCandle.low <= 0,
                OhlcvCandle.close <= 0,
            )
        )
    )
    return int(result.rowcount or 0)


async def prune_instruments_not_in_universe(
    session: AsyncSession,
    allowed_symbols: set[str],
) -> dict[str, int]:
    """Remove delisted / non-universe equities and all their OHLCV rows."""
    allowed = {s.upper() for s in allowed_symbols}
    instruments = list(await session.scalars(select(Instrument)))

    candles_deleted = 0
    instruments_deleted = 0
    instruments_deactivated = 0

    for inst in instruments:
        if inst.instrument_type == InstrumentType.INDEX:
            continue
        if inst.symbol in allowed:
            continue

        has_refs = await _instrument_has_paper_refs(session, inst.id)
        if has_refs:
            # Keep OHLCV for held symbols outside NIFTY250 (e.g. CDSL); just mark inactive.
            inst.is_active = False
            instruments_deactivated += 1
            continue

        candle_result = await session.execute(
            delete(OhlcvCandle).where(OhlcvCandle.instrument_id == inst.id)
        )
        candles_deleted += int(candle_result.rowcount or 0)
        await session.delete(inst)
        instruments_deleted += 1

    return {
        "candles_deleted": candles_deleted,
        "instruments_deleted": instruments_deleted,
        "instruments_deactivated": instruments_deactivated,
    }


async def reconcile_market_data_universe(
    session: AsyncSession,
    *,
    progress_callback=None,
) -> dict:
    """Refresh NIFTY constituents, trim date range, and drop delisted symbols."""
    universe = market_data_universe()

    if progress_callback:
        progress_callback(0, 1, f"Loading {universe} constituent list…")

    symbols = ensure_universe_symbols_fresh(universe)

    allowed = {s.upper() for s in symbols}
    allowed.update(await _open_position_symbols(session))
    start, end = market_data_date_range()

    if progress_callback:
        progress_callback(
            0,
            1,
            f"Trimming OHLCV to {start.isoformat()} – {end.isoformat()}…",
        )
    candles_trimmed = await prune_candles_outside_range(session, start, end)

    if progress_callback:
        progress_callback(0, 1, "Removing invalid OHLCV rows (NaN/zero)…")
    invalid_candles = await prune_invalid_candles(session)

    if progress_callback:
        progress_callback(0, 1, "Removing delisted / non-universe stocks…")
    prune_stats = await prune_instruments_not_in_universe(session, allowed)

    await ensure_market_data_instruments(session)
    await session.commit()

    equity_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Instrument)
            .where(Instrument.instrument_type == InstrumentType.EQUITY)
        )
        or 0
    )

    return {
        "universe": universe,
        "allowed_symbols": len(allowed),
        "equity_instruments": equity_count,
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "candles_trimmed": candles_trimmed,
        "invalid_candles_deleted": invalid_candles,
        **prune_stats,
    }


async def _latest_candle_dates(
    session: AsyncSession,
    instrument_ids: list[int],
) -> dict[int, date | None]:
    if not instrument_ids:
        return {}
    rows = (
        await session.execute(
            select(OhlcvCandle.instrument_id, func.max(OhlcvCandle.trade_date)).where(
                OhlcvCandle.instrument_id.in_(instrument_ids)
            ).group_by(OhlcvCandle.instrument_id)
        )
    ).all()
    return {int(iid): max_date for iid, max_date in rows}


async def _earliest_candle_dates(
    session: AsyncSession,
    instrument_ids: list[int],
) -> dict[int, date | None]:
    if not instrument_ids:
        return {}
    rows = (
        await session.execute(
            select(OhlcvCandle.instrument_id, func.min(OhlcvCandle.trade_date)).where(
                OhlcvCandle.instrument_id.in_(instrument_ids)
            ).group_by(OhlcvCandle.instrument_id)
        )
    ).all()
    return {int(iid): min_date for iid, min_date in rows}


def _missing_fetch_ranges(
    *,
    window_start: date,
    end: date,
    latest: date | None,
    earliest: date | None = None,
) -> list[tuple[date, date]]:
    """Return date ranges that still need downloading for one instrument."""
    if latest is None:
        return [(window_start, end)]
    ranges: list[tuple[date, date]] = []
    if earliest is not None and earliest > window_start:
        ranges.append((window_start, earliest - timedelta(days=1)))
    if latest < end:
        ranges.append((latest + timedelta(days=1), end))
    return ranges


async def backfill_symbol_if_missing(session: AsyncSession, symbol: str) -> int:
    """Fetch OHLCV for one symbol when missing (e.g. small-cap open position)."""
    sym = symbol.upper()
    instrument = await session.scalar(select(Instrument).where(Instrument.symbol == sym))
    if instrument is None:
        await ensure_market_data_instruments(session)
        instrument = await session.scalar(select(Instrument).where(Instrument.symbol == sym))
    if instrument is None:
        return 0

    existing = await session.scalar(
        select(func.count())
        .select_from(OhlcvCandle)
        .where(OhlcvCandle.instrument_id == instrument.id)
    )
    if existing and int(existing) > 0:
        return 0

    end = market_data_sync_end_date()
    window_start = end - timedelta(days=effective_backfill_days())
    provider = get_market_data_provider()
    candles = await provider.fetch_candles(
        provider_market_symbol(instrument),
        window_start,
        end,
    )
    if not candles:
        return 0
    count = await upsert_candles(session, instrument.id, candles)
    await session.commit()
    return count


async def backfill_candles(
    session: AsyncSession | None = None,
    days: int | None = None,
    progress_callback=None,
    *,
    reconcile: bool = True,
) -> dict:
    """Pull only missing OHLCV from NSE — the sole internet-facing ingestion path."""
    provider = get_market_data_provider()
    backfill_days = effective_backfill_days(days)
    end = market_data_sync_end_date()
    window_start = end - timedelta(days=backfill_days)
    universe = market_data_universe()

    reconcile_stats: dict = {}
    if reconcile:
        async with db_session() as reconcile_session:
            reconcile_stats = await reconcile_market_data_universe(
                reconcile_session,
                progress_callback=progress_callback,
            )

    async with db_session() as list_session:
        instruments = await _market_data_instruments(list_session)
        instrument_ids = [inst.id for inst in instruments]
        latest_by_id = await _latest_candle_dates(list_session, instrument_ids)
        earliest_by_id = await _earliest_candle_dates(list_session, instrument_ids)

    synced = 0
    fetched_symbols = 0
    skipped_symbols = 0
    total = len(instruments)
    for i, instrument in enumerate(instruments, start=1):
        latest = latest_by_id.get(instrument.id)
        fetch_ranges = _missing_fetch_ranges(
            window_start=window_start,
            end=end,
            latest=latest,
            earliest=earliest_by_id.get(instrument.id),
        )
        if not fetch_ranges:
            skipped_symbols += 1
            if progress_callback:
                progress_callback(
                    i,
                    total,
                    f"Up to date · {instrument.symbol} ({i}/{total})",
                )
            continue

        fetched_symbols += 1
        if progress_callback:
            progress_callback(
                i,
                total,
                f"Fetching missing data · {instrument.symbol} ({i}/{total}) · {universe}…",
            )

        for fetch_start, fetch_end in fetch_ranges:
            if fetch_start > fetch_end:
                continue
            candles = await provider.fetch_candles(
                provider_market_symbol(instrument), fetch_start, fetch_end
            )
            if not candles:
                continue
            candles = [c for c in candles if window_start <= c.trade_date <= end]
            if not candles:
                continue
            async with db_session() as write_session:
                synced += await upsert_candles(write_session, instrument.id, candles)
                await write_session.commit()

    async with db_session() as trading_session:
        trading_service = PaperTradingService(trading_session)
        await trading_service.match_pending_limit_orders()
        from app.services.market_calendar import current_session_date, is_live_quote_session
        from app.services.trade_plans import TradePlanService

        plan_service = TradePlanService(trading_session)
        # During live hours, skip EOD bracket processing on prior sessions — intraday
        # target/stop/3:25 exits are handled by live polling instead.
        run_eod = not (is_live_quote_session() and end < current_session_date())
        if run_eod:
            await plan_service.process_eod(end)

    prune_stats: dict | None = None
    async with db_session() as prune_session:
        from app.services.paper_trading_retention import prune_paper_trading_history_if_due

        prune_stats = await prune_paper_trading_history_if_due(prune_session)

    result = {
        "universe": universe,
        "instruments": len(instruments),
        "instruments_fetched": fetched_symbols,
        "instruments_skipped": skipped_symbols,
        "candles_upserted": synced,
        "days": backfill_days,
        "data_through": end.isoformat(),
        **reconcile_stats,
    }
    if prune_stats is not None:
        result["paper_trading_prune"] = prune_stats
    return result


async def sync_latest(
    session: AsyncSession | None = None,
    progress_callback=None,
) -> dict:
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent

    async with audit_track(
        "ingestion.sync_latest",
        AuditComponent.INGESTION,
        universe=market_data_universe(),
        days=settings.backfill_days,
    ):
        log.info(
            "Market sync starting universe=%s backfill_days=%s",
            market_data_universe(),
            settings.backfill_days,
        )
        result = await backfill_candles(
            days=settings.backfill_days,
            progress_callback=progress_callback,
            reconcile=True,
        )
        log.info(
            "Market sync finished inserted=%s data_through=%s",
            result.get("inserted"),
            result.get("data_through"),
        )
        try:
            from app.services.market_sync_status import record_market_sync_success

            record_market_sync_success(result.get("data_through"))
        except Exception:
            log.debug("Could not persist market sync status", exc_info=True)
        return result
