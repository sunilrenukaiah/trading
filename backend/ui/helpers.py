"""Async helpers for the Streamlit UI."""

import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.ui_session import ui_session
from app.models import Instrument, OhlcvCandle, PaperOrder, PaperTradePlan, TradePlanStatus
from app.schemas import (
    AccountOut,
    CandleOut,
    InstrumentOut,
    MarketSummaryItem,
    OrderOut,
    OrderSide,
    OrderType,
    PlaceOrderRequest,
    PositionOut,
    TradeOut,
)
from app.services.ingestion import seed_instruments, seed_paper_account, sync_latest
from app.services.market_summary import fetch_market_summary
from app.services.ohlcv_utils import finite_decimal
from app.services.market_data_stats import MarketDataStats
from app.services.paper_trading import PaperTradingService

BACKEND_DIR = Path(__file__).resolve().parent.parent


from ui.async_runner import run_async


def _ui_scheduled_jobs_disabled() -> bool:
    """When set (Streamlit Cloud secrets), cron is handled by GitHub Actions instead."""
    import os

    raw = os.environ.get("DISABLE_UI_SCHEDULED_SYNC", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _prepare_database_url_from_runtime() -> None:
    """Map Streamlit secrets → env and normalize Neon URLs for asyncpg."""
    import os

    try:
        import streamlit as st

        for key in (
            "DATABASE_URL",
            "DATA_PROVIDER",
            "DAILY_TRADING_BUDGET_INR",
            "BACKFILL_DAYS",
            "MARKET_DATA_UNIVERSE",
            "DISABLE_UI_SCHEDULED_SYNC",
        ):
            try:
                if key in st.secrets:
                    os.environ[key] = str(st.secrets[key])
            except Exception:
                pass
    except Exception:
        pass

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return
    if url.startswith("postgresql://") and "+asyncpg" not in url.split("://", 1)[0]:
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
    url = url.replace("sslmode=require", "ssl=require")
    url = url.replace("sslmode=required", "ssl=require")
    os.environ["DATABASE_URL"] = url

    from app.config import settings

    settings.database_url = url

    # Drop lazy UI engine so the next session uses the updated URL.
    try:
        from app.db import ui_session as ui_sess

        ui_sess._ui_engine = None  # noqa: SLF001
        ui_sess.UISessionLocal = None
    except Exception:
        pass


def run_migrations() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
        capture_output=True,
    )


async def _bootstrap_if_needed() -> None:
    async with ui_session() as session:
        await seed_instruments(session)
        await seed_paper_account(session)


def ensure_ready() -> bool:
    try:
        import streamlit as st
    except Exception:
        st = None

    if st is not None and st.session_state.get("_db_ready_checked"):
        return bool(st.session_state.get("_db_ready", False))

    ready = False
    try:
        if st is not None:
            st.caption("Connecting to database…")
        _prepare_database_url_from_runtime()

        from app.logging_setup import configure_app_logging
        from app.services.applicable_rates import get_applicable_rates
        from app.services.audit_handlers import install_audit_hooks

        configure_app_logging()
        install_audit_hooks()
        get_applicable_rates()
        if st is not None:
            st.caption("Running database migrations…")
        run_migrations()
        if st is not None:
            st.caption("Seeding paper account…")
        # Keep bootstrap short — do not wait an hour on a bad DB URL.
        run_async(_bootstrap_if_needed(), timeout=120, retries=0)
        ready = True
    except Exception:
        ready = False

    if st is not None:
        try:
            st.session_state["_db_ready_checked"] = True
            st.session_state["_db_ready"] = ready
        except Exception:
            pass
    return ready


async def _list_instruments() -> list[InstrumentOut]:
    async with ui_session() as session:
        return await _list_instruments_for(session)


async def _list_instruments_for(session) -> list[InstrumentOut]:
    instruments = (
        await session.scalars(
            select(Instrument)
            .where(Instrument.is_active.is_(True))
            .order_by(Instrument.instrument_type, Instrument.symbol)
        )
    ).all()
    return [InstrumentOut.model_validate(i) for i in instruments]


async def _list_chart_instruments_for(session) -> list[InstrumentOut]:
    """All instruments in DB for chart symbol picker (not limited to is_active NIFTY50)."""
    instruments = (
        await session.scalars(
            select(Instrument).order_by(Instrument.instrument_type, Instrument.symbol)
        )
    ).all()
    return [InstrumentOut.model_validate(i) for i in instruments]


async def _market_summary() -> list[MarketSummaryItem]:
    async with ui_session() as session:
        return await _market_summary_for(session)


async def _market_summary_for(session) -> list[MarketSummaryItem]:
    return await fetch_market_summary(session)


async def _load_trading_page_data(
    budget_inr: float | None = None,
    *,
    include_summary: bool = False,
    include_md_stats: bool = False,
) -> tuple[list[InstrumentOut], AccountOut, list[MarketSummaryItem], MarketDataStats | None, list[PositionOut]]:
    """One session, one coroutine — avoids asyncpg overlap between page queries."""
    from app.services.budget_portfolio import budget_from_settings, normalize_legacy_paper_account
    from ui.streamlit_imports import ensure_market_data_stats_fresh

    ensure_market_data_stats_fresh()
    from app.services.market_data_stats import get_market_data_stats

    budget = budget_inr if budget_inr is not None else budget_from_settings()

    async with ui_session() as session:
        await normalize_legacy_paper_account(session, budget)
        service = PaperTradingService(session)
        instruments = await _list_chart_instruments_for(session)
        account = await service.get_account_summary()
        positions = await service.list_positions()
        summary = await _market_summary_for(session) if include_summary else []
        md_stats = (
            await get_market_data_stats("NIFTY250", session=session)
            if include_md_stats
            else None
        )
        return instruments, account, summary, md_stats, positions


async def _candles(symbol: str, days: int) -> list[CandleOut]:
    async with ui_session() as session:
        instrument = await session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        if not instrument:
            return []

        start_date = date.today() - timedelta(days=days)
        rows = (
            await session.scalars(
                select(OhlcvCandle)
                .where(
                    OhlcvCandle.instrument_id == instrument.id,
                    OhlcvCandle.trade_date >= start_date,
                )
                .order_by(OhlcvCandle.trade_date.asc())
            )
        ).all()
        out: list[CandleOut] = []
        for c in rows:
            candle = _ohlcv_to_candle_out(c)
            if candle is not None:
                out.append(candle)
        return out


async def _nifty250_index_candles(days: int = 30) -> list[CandleOut]:
    from app.services.nifty250_index import load_nifty250_composite_candles

    async with ui_session() as session:
        return await load_nifty250_composite_candles(session, days=days)


def _ohlcv_to_candle_out(c: OhlcvCandle) -> CandleOut | None:
    """Map ORM row to schema — skips corrupt NaN/zero rows."""
    open_ = finite_decimal(c.open)
    high = finite_decimal(c.high)
    low = finite_decimal(c.low)
    close = finite_decimal(c.close)
    if open_ is None or high is None or low is None or close is None:
        return None
    return CandleOut(
        trade_date=c.trade_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=c.volume,
    )


async def _realized_pnl_after_tax_summary():
    from sqlalchemy import select

    from app.models import PaperTrade
    from app.schemas import OrderSide
    from ui.streamlit_imports import ensure_defaults_fresh, ensure_trade_tax_fresh

    ensure_defaults_fresh()
    ensure_trade_tax_fresh()
    from app.services.trade_tax import summarize_sell_trades_dual_broker

    async with ui_session() as session:
        svc = PaperTradingService(session)
        account = await svc.get_default_account()
        trades = (
            await session.scalars(
                select(PaperTrade).where(
                    PaperTrade.account_id == account.id,
                    PaperTrade.side == OrderSide.SELL,
                )
            )
        ).all()
        sells: list[tuple[int, float, float]] = []
        for trade in trades:
            qty = int(trade.quantity)
            if qty <= 0:
                continue
            sell_price = float(trade.price)
            gross = float(trade.realized_pnl)
            buy_price = sell_price - gross / qty
            sells.append((qty, buy_price, sell_price))
        return summarize_sell_trades_dual_broker(sells)


async def _account() -> AccountOut:
    async with ui_session() as session:
        return await PaperTradingService(session).get_account_summary()


async def _positions() -> list[PositionOut]:
    async with ui_session() as session:
        return await PaperTradingService(session).list_positions()


async def _recommendation_bracket_symbols() -> set[str]:
    async with ui_session() as session:
        return await PaperTradingService(session).recommendation_position_symbols()


async def _orders(*, session_date=None) -> list[OrderOut]:
    from app.models import OrderStatus
    from app.services.market_calendar import current_session_date

    day = session_date or current_session_date()
    async with ui_session() as session:
        rows = await PaperTradingService(session).list_orders(session_date=day)
    return [row for row in rows if row.status != OrderStatus.CANCELLED]


async def _cleanup_duplicate_session_orders() -> dict[str, int]:
    from app.services.trade_plans import TradePlanService

    async with ui_session() as session:
        return await TradePlanService(session).cleanup_duplicate_session_plans()


async def _trades(*, session_date=None) -> list[TradeOut]:
    from app.services.market_calendar import current_session_date

    day = session_date or current_session_date()
    async with ui_session() as session:
        return await PaperTradingService(session).list_trades(session_date=day)


async def _recommended_symbols_for_session(session_date=None) -> set[str]:
    from app.services.market_calendar import active_market_session_date
    from app.services.recommendation_cache import recommended_symbols_for_prediction_date

    day = session_date or active_market_session_date()
    async with ui_session() as session:
        return await recommended_symbols_for_prediction_date(session, day)


async def _fetch_live_quotes(symbols: list[str]) -> dict[str, Decimal]:
    from app.services.live_quotes import fetch_live_quotes

    async with ui_session() as session:
        quotes = await fetch_live_quotes(session, symbols)
        return {sym: q.last_price for sym, q in quotes.items()}


async def _place_order(
    request: PlaceOrderRequest,
    budget_inr: float | None = None,
    *,
    market_fill_price: Decimal | None = None,
) -> OrderOut:
    from app.services.budget_portfolio import budget_from_settings, validate_buy_against_budget

    budget = budget_inr if budget_inr is not None else budget_from_settings()

    async with ui_session() as session:
        service = PaperTradingService(session)
        if request.side == OrderSide.SELL:
            bracket_symbols = await service.recommendation_position_symbols()
            if request.symbol.upper() in {s.upper() for s in bracket_symbols}:
                raise ValueError(
                    f"{request.symbol.upper()} has an active recommendation bracket — "
                    "sell only via target, stop loss, or 3:25 PM square-off."
                )
        if request.side == OrderSide.BUY:
            positions = await service.list_positions()
            instrument = await session.scalar(
                select(Instrument).where(Instrument.symbol == request.symbol.upper())
            )
            if instrument:
                price = await service._latest_close(instrument.id)
                if price is not None:
                    validate_buy_against_budget(
                        budget, positions, float(price * request.quantity)
                    )
        order = await service.place_order(request, market_fill_price=market_fill_price)
        loaded = await session.scalar(
            select(PaperOrder)
            .where(PaperOrder.id == order.id)
            .options(selectinload(PaperOrder.instrument))
        )
        return service.order_to_schema(loaded)


@dataclass(frozen=True)
class MiddayBudgetContext:
    morning_budget_inr: float
    invested_cost: float
    session_realized_pnl: float
    available_inr: float


async def _midday_budget_context() -> MiddayBudgetContext:
    """Morning base budget minus open position cost and today's realized P&L."""
    from app.services.budget_portfolio import budget_from_settings, compute_base_budget_available
    from app.services.market_calendar import current_session_date
    from app.services.recommendation_cache import load_cached_recommendations_for_ui

    morning_budget = budget_from_settings()
    cached = await load_cached_recommendations_for_ui()
    if cached is not None:
        morning_budget = float(cached[2])

    positions = await _positions()
    async with ui_session() as session:
        day_pnl = float(
            await PaperTradingService(session).day_realized_pnl_from_trades(
                current_session_date()
            )
        )
    view = compute_base_budget_available(morning_budget, positions, day_pnl)
    return MiddayBudgetContext(
        morning_budget_inr=view.morning_budget_inr,
        invested_cost=view.invested_cost,
        session_realized_pnl=view.session_realized_pnl,
        available_inr=view.cash_available,
    )


async def _place_midday_allocation_buy(
    symbol: str,
    shares: int,
    budget_inr: float | None = None,
    *,
    recommendation_date: date | None = None,
    buy_price: float | None = None,
    stop_loss: float | None = None,
    target_price: float | None = None,
    pattern_name: str | None = None,
    morning_budget_inr: float | None = None,
    session_realized_pnl: float | None = None,
) -> OrderOut:
    """Place or calibrate a mid-day bracket recommendation on button click."""
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent
    from app.services.budget_allocator import AllocationLine
    from app.services.ingestion import ensure_market_data_instruments
    from app.services.market_calendar import active_market_session_date
    from app.services.trade_plans import TradePlanService

    rec_date = recommendation_date or active_market_session_date()
    if buy_price is None or stop_loss is None or target_price is None:
        raise ValueError("buy_price, stop_loss, and target_price are required for bracket orders")

    line = AllocationLine(
        symbol=symbol.upper(),
        cap_tier="",
        shares=int(shares),
        buy_price=float(buy_price),
        investment=round(float(buy_price) * int(shares), 2),
        stop_loss=float(stop_loss),
        model_target_price=float(target_price),
        actual_sell_price=float(target_price),
        expected_profit=0,
        gross_profit=0,
        profit_before_tax=0,
        total_charges=0,
        stcg_tax=0,
        net_profit_after_tax=0,
        max_loss=0,
        weight_pct=0,
        pattern_name=pattern_name or "",
        confidence_score=0,
    )

    async with audit_track(
        "recommendation.midday_place",
        AuditComponent.UI,
        symbol=line.symbol,
        shares=line.shares,
        recommendation_date=str(rec_date),
        buy_price=line.buy_price,
        target_price=line.actual_sell_price,
        stop_loss=line.stop_loss,
    ):
        if morning_budget_inr is None or session_realized_pnl is None:
            ctx = await _midday_budget_context()
            morning_budget_inr = ctx.morning_budget_inr
            session_realized_pnl = ctx.session_realized_pnl
        async with ui_session() as session:
            await ensure_market_data_instruments(session)
            service = TradePlanService(session)
            plan = await service.apply_midday_recommendation(
                line,
                rec_date,
                budget_inr=morning_budget_inr,
                session_realized_pnl=session_realized_pnl,
            )
            order = await session.scalar(
                select(PaperOrder)
                .where(PaperOrder.id == plan.entry_order_id)
                .options(selectinload(PaperOrder.instrument))
            )
            if order is None:
                raise ValueError("Entry order not found after mid-day placement")
            return PaperTradingService(session).order_to_schema(order)


async def _place_all_midday_orders(
    allocation,
    recommendation_date: date,
    budget_inr: float | None = None,
    *,
    symbols: list[str] | None = None,
    morning_budget_inr: float | None = None,
    session_realized_pnl: float | None = None,
) -> list[tuple[str, str, str]]:
    if morning_budget_inr is None or session_realized_pnl is None:
        ctx = await _midday_budget_context()
        morning_budget_inr = ctx.morning_budget_inr
        session_realized_pnl = ctx.session_realized_pnl

    lines = allocation.lines
    if symbols is not None:
        allowed = {s.upper() for s in symbols}
        lines = [line for line in lines if line.symbol.upper() in allowed]

    applied, _ = await _load_midday_place_state(recommendation_date, allocation)
    lines = [line for line in lines if line.symbol.upper() not in applied]

    results: list[tuple[str, str, str]] = []
    for line in lines:
        try:
            await _place_midday_allocation_buy(
                line.symbol,
                line.shares,
                budget_inr,
                recommendation_date=recommendation_date,
                buy_price=line.buy_price,
                stop_loss=line.stop_loss,
                target_price=line.actual_sell_price,
                pattern_name=line.pattern_name,
                morning_budget_inr=morning_budget_inr,
                session_realized_pnl=session_realized_pnl,
            )
            results.append((line.symbol, "placed", ""))
        except Exception as exc:
            results.append((line.symbol, "error", str(exc)))
    return results


async def _place_allocation_buy(
    symbol: str,
    shares: int,
    budget_inr: float | None = None,
    *,
    recommendation_date: date | None = None,
    buy_price: float | None = None,
    stop_loss: float | None = None,
    target_price: float | None = None,
    pattern_name: str | None = None,
) -> OrderOut:
    """Place a bracket trade plan: limit BUY at recommended price, auto target/stop."""
    from app.services.budget_allocator import AllocationLine
    from app.services.ingestion import ensure_market_data_instruments
    from app.services.market_calendar import last_completed_trading_day
    from app.services.trade_plans import TradePlanService

    rec_date = recommendation_date or last_completed_trading_day()
    if buy_price is None or stop_loss is None or target_price is None:
        raise ValueError("buy_price, stop_loss, and target_price are required for bracket orders")

    line = AllocationLine(
        symbol=symbol.upper(),
        cap_tier="",
        shares=int(shares),
        buy_price=float(buy_price),
        investment=round(float(buy_price) * int(shares), 2),
        stop_loss=float(stop_loss),
        model_target_price=float(target_price),
        actual_sell_price=float(target_price),
        expected_profit=0,
        gross_profit=0,
        profit_before_tax=0,
        total_charges=0,
        stcg_tax=0,
        net_profit_after_tax=0,
        max_loss=0,
        weight_pct=0,
        pattern_name=pattern_name or "",
        confidence_score=0,
    )

    async with ui_session() as session:
        await ensure_market_data_instruments(session)
        service = TradePlanService(session)
        plan = await service.place_recommendation_plan(line, rec_date, budget_inr=budget_inr)
        order = await session.scalar(
            select(PaperOrder)
            .where(PaperOrder.id == plan.entry_order_id)
            .options(selectinload(PaperOrder.instrument))
        )
        if order is None:
            raise ValueError("Entry order not found after placing trade plan")
        return PaperTradingService(session).order_to_schema(order)


async def _place_all_allocation_orders(
    allocation,
    recommendation_date: date,
    budget_inr: float | None = None,
    *,
    symbols: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    from app.services.ingestion import ensure_market_data_instruments
    from app.services.trade_plans import TradePlanService

    lines = allocation.lines
    if symbols is not None:
        pending = {s.upper() for s in symbols}
        lines = [line for line in lines if line.symbol.upper() in pending]

    async with ui_session() as session:
        await ensure_market_data_instruments(session)
        service = TradePlanService(session)
        raw = await service.place_all_recommendation_plans(
            lines,
            recommendation_date,
            budget_inr=budget_inr,
        )
    return [(sym, status, str(detail)) for sym, status, detail in raw]


async def _reconcile_brackets_after_downtime() -> dict[str, object]:
    """Catch up target/stop/entry fills missed while the UI process was stopped."""
    from app.services.trade_plans import TradePlanService

    async with ui_session() as session:
        return await TradePlanService(session).reconcile_session_brackets_after_downtime()


async def _reconcile_brackets_if_needed(*, force: bool = False) -> dict[str, object] | None:
    """Run bracket catch-up when stale or forced; persist timestamp on success."""
    from app.services.bracket_reconcile_state import (
        record_reconcile_success,
        should_auto_reconcile,
    )
    from app.services.market_calendar import current_session_date
    from app.services.trade_plans import TradePlanService

    if not force and not should_auto_reconcile():
        return None

    async with ui_session() as session:
        stats = await TradePlanService(session).reconcile_session_brackets_after_downtime()
    record_reconcile_success(session_date=current_session_date())
    return stats


async def _get_eod_analysis(recommendation_date: date, as_of_date: date | None = None):
    from app.services.trade_plans import TradePlanService

    async with ui_session() as session:
        return await TradePlanService(session).build_eod_analysis(
            recommendation_date,
            as_of_date=as_of_date,
        )


async def _list_eod_trade_dates() -> list[date]:
    from app.services.eod_trade_analysis import EodTradeAnalysisService

    async with ui_session() as session:
        return await EodTradeAnalysisService(session).list_trade_dates()


async def _run_eod_trade_analysis(trade_date: date | None = None):
    from app.services.eod_trade_analysis import EodTradeAnalysisService

    async with ui_session() as session:
        return await EodTradeAnalysisService(session).build_report(trade_date)


async def _load_paper_trading_trend():
    from app.services.paper_trading_trend import PaperTradingTrendService

    async with ui_session() as session:
        return await PaperTradingTrendService(session).build_report()


_TRADE_PLAN_STATUS_LABELS = {
    TradePlanStatus.PENDING_ENTRY: "Pending entry",
    TradePlanStatus.OPEN: "Open",
    TradePlanStatus.TARGET_HIT: "Target hit",
    TradePlanStatus.STOP_HIT: "Stop hit",
    TradePlanStatus.TIME_EXIT: "3:25 PM exit",
    TradePlanStatus.CANCELLED: "Cancelled",
}


async def _load_order_bracket_context(
    recommendation_date: date,
) -> tuple[dict[int, tuple[float, float]], dict[str, tuple[float, float]]]:
    """Map order id / symbol to (target buy, target sell) from bracket trade plans."""
    from zoneinfo import ZoneInfo

    from app.services.market_calendar import active_market_session_date, current_session_date
    from app.services.paper_trading import PaperTradingService

    ist = ZoneInfo("Asia/Kolkata")
    check_dates = {recommendation_date, active_market_session_date()}
    session_day = current_session_date()

    async with ui_session() as session:
        account = await PaperTradingService(session).get_default_account()
        plans = (
            await session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.status != TradePlanStatus.CANCELLED,
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()

        session_plans: list[PaperTradePlan] = []
        for plan in plans:
            if plan.instrument is None:
                continue
            if plan.recommendation_date in check_dates:
                session_plans.append(plan)
                continue
            if plan.entry_order_id is None:
                continue
            order = await session.get(PaperOrder, plan.entry_order_id)
            if order is not None:
                created = order.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=ist)
                else:
                    created = created.astimezone(ist)
                if created.date() == session_day:
                    session_plans.append(plan)

    by_order: dict[int, tuple[float, float]] = {}
    by_symbol: dict[str, tuple[float, float]] = {}
    for plan in session_plans:
        sym = plan.instrument.symbol
        pair = (float(plan.entry_limit_price), float(plan.target_price))
        by_symbol[sym] = pair
        if plan.entry_order_id:
            by_order[plan.entry_order_id] = pair
        if plan.exit_order_id:
            by_order[plan.exit_order_id] = pair
    return by_order, by_symbol


async def _load_position_bracket_levels() -> dict[str, tuple[float, float]]:
    """symbol -> (target_price, stop_loss_price) for active bracket trade plans."""
    from app.services.paper_trading import PaperTradingService
    from ui.streamlit_imports import import_module_safe

    trade_plans = import_module_safe("app.services.trade_plans")
    TradePlanService = trade_plans.TradePlanService

    async with ui_session() as session:
        paper = PaperTradingService(session)
        await paper.match_pending_limit_orders()
        await TradePlanService(session)._sync_entries_from_orders()

        account = await paper.get_default_account()
        plans = (
            await session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.status.in_(
                        (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
                    ),
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
    return {
        plan.instrument.symbol: (float(plan.target_price), float(plan.stop_loss_price))
        for plan in plans
        if plan.instrument is not None
    }


async def _load_position_chart_context(
    symbol: str,
    *,
    live_price: float | None = None,
    mark_price: float | None = None,
):
    from app.services.intraday_chart import build_position_intraday_context

    async with ui_session() as session:
        return await build_position_intraday_context(
            session,
            symbol,
            live_price=live_price,
            mark_price=mark_price,
        )


async def _load_trade_plan_status_map(recommendation_date: date) -> dict[str, str]:
    """Symbol -> human-readable plan status for a recommendation date."""
    placed, status_map = await _load_allocation_trade_plan_state(
        recommendation_date,
        [],
    )
    del placed
    return status_map


async def _load_placed_allocation_symbols(
    recommendation_date: date,
    line_symbols: list[str] | None = None,
) -> set[str]:
    """Symbols with a non-cancelled bracket plan for this recommendation session."""
    placed, _ = await _load_allocation_trade_plan_state(
        recommendation_date,
        line_symbols or [],
    )
    return placed


async def _load_allocation_trade_plan_state(
    recommendation_date: date,
    line_symbols: list[str],
) -> tuple[set[str], dict[str, str]]:
    """Symbols already traded from allocation + plan status labels for the UI."""
    from zoneinfo import ZoneInfo

    from app.services.market_calendar import active_market_session_date, current_session_date
    from app.services.paper_trading import PaperTradingService

    IST = ZoneInfo("Asia/Kolkata")
    sym_filter = {s.upper() for s in line_symbols} if line_symbols else None
    check_dates = {recommendation_date, active_market_session_date()}
    session_day = current_session_date()
    placed: set[str] = set()
    status_map: dict[str, str] = {}

    async with ui_session() as session:
        account = await PaperTradingService(session).get_default_account()
        plans = (
            await session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.account_id == account.id)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()

        for plan in plans:
            if plan.instrument is None or plan.status == TradePlanStatus.CANCELLED:
                continue
            sym = plan.instrument.symbol
            if sym_filter is not None and sym.upper() not in sym_filter:
                continue

            matched = plan.recommendation_date in check_dates
            if not matched and plan.entry_order_id is not None:
                order = await session.get(PaperOrder, plan.entry_order_id)
                if order is not None and order.created_at is not None:
                    created = order.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=IST)
                    else:
                        created = created.astimezone(IST)
                    if created.date() == session_day:
                        matched = True

            if not matched:
                continue

            placed.add(sym)
            status_map[sym] = _TRADE_PLAN_STATUS_LABELS.get(
                plan.status, plan.status.value.replace("_", " ").title()
            )

    return placed, status_map


async def _load_midday_place_state(
    recommendation_date: date,
    allocation,
) -> tuple[set[str], dict[str, str]]:
    """Mid-day symbols already placed/calibrated + plan status labels for the UI."""
    from zoneinfo import ZoneInfo

    from app.services.market_calendar import active_market_session_date, current_session_date
    from app.services.midday_recommendations import (
        action_kind_for_plan_status,
        is_midday_action_applied,
    )
    from app.services.paper_trading import PaperTradingService

    IST = ZoneInfo("Asia/Kolkata")
    line_by_symbol = {line.symbol.upper(): line for line in allocation.lines}
    sym_filter = set(line_by_symbol)
    check_dates = {recommendation_date, active_market_session_date()}
    session_day = current_session_date()
    applied: set[str] = set()
    status_map: dict[str, str] = {}
    plans_by_symbol: dict[str, PaperTradePlan] = {}

    async with ui_session() as session:
        account = await PaperTradingService(session).get_default_account()
        plans = (
            await session.scalars(
                select(PaperTradePlan)
                .where(PaperTradePlan.account_id == account.id)
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()

        for plan in plans:
            if plan.instrument is None or plan.status == TradePlanStatus.CANCELLED:
                continue
            sym = plan.instrument.symbol.upper()
            if sym not in sym_filter:
                continue

            matched = plan.recommendation_date in check_dates
            if not matched and plan.entry_order_id is not None:
                order = await session.get(PaperOrder, plan.entry_order_id)
                if order is not None and order.created_at is not None:
                    created = order.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=IST)
                    else:
                        created = created.astimezone(IST)
                    if created.date() == session_day:
                        matched = True

            if not matched:
                continue

            plans_by_symbol[sym] = plan
            status_map[plan.instrument.symbol] = _TRADE_PLAN_STATUS_LABELS.get(
                plan.status, plan.status.value.replace("_", " ").title()
            )

        for sym, line in line_by_symbol.items():
            plan_status = status_map.get(sym) or status_map.get(line.symbol)
            action = action_kind_for_plan_status(plan_status)
            plan = plans_by_symbol.get(sym)
            if is_midday_action_applied(plan, line, action):
                applied.add(sym)

    return applied, status_map


async def _refresh_live_trading(
    extra_symbols: list[str] | None = None,
) -> tuple[dict[str, dict[str, float | None]], dict[str, int]]:
    """Fetch NSE LTP for open bracket plans + positions; run intraday entry/exit checks."""
    from app.services.live_quotes import PositionLiveQuote, fetch_live_quotes
    from app.services.trade_plans import TradePlanService

    async with ui_session() as session:
        plans = (
            await session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.status.in_(
                        (TradePlanStatus.PENDING_ENTRY, TradePlanStatus.OPEN)
                    )
                )
                .options(selectinload(PaperTradePlan.instrument))
            )
        ).all()
        symbols = {p.instrument.symbol for p in plans if p.instrument is not None}
        symbols.update(s.upper() for s in (extra_symbols or []) if s)
        if not symbols:
            return {}, {}

        quotes = await fetch_live_quotes(session, sorted(symbols))
        stats: dict[str, int] = {}
        if quotes:
            from app.services.market_calendar import is_square_off_window
            from app.services.paper_trading import PaperTradingService

            stats = await TradePlanService(session).process_live_quotes(quotes)
            if is_square_off_window():
                paper = PaperTradingService(session)
                ltp_map = {sym: q.last_price for sym, q in quotes.items()}
                remaining = await paper.square_off_remaining_positions(ltp_map)
                if remaining:
                    stats["remaining_square_offs"] = remaining
        cache = {
            sym: PositionLiveQuote.from_session_quote(q).to_cache()
            for sym, q in quotes.items()
        }
        return cache, stats


async def _process_trade_plans_live() -> dict[str, int]:
    _, stats = await _refresh_live_trading()
    return stats


async def _cancel_order(order_id: int) -> None:
    async with ui_session() as session:
        await PaperTradingService(session).cancel_order(order_id)


async def _sync_market() -> dict:
    return await sync_latest()


async def _market_data_stats(universe: str = "NIFTY250"):
    from app.services.market_data_stats import get_market_data_stats

    return await get_market_data_stats(universe)


async def _load_cached_simulation(universe: str):
    """Load today's cached simulation for universe from DB, if present."""
    from app.services.simulation_cache import load_cached_simulation

    return await load_cached_simulation(universe)


async def _ensure_simulation_candle_data(
    universe: str,
    progress_callback=None,
) -> bool:
    """Backfill OHLCV from NSE when local DB lacks enough history for simulation."""
    from app.services.backtest import count_symbols_ready_for_simulation

    async with ui_session() as session:
        ready, _total, min_bars = await count_symbols_ready_for_simulation(session, universe)

    if ready > 0:
        return True

    if progress_callback:
        progress_callback(
            f"Need {min_bars} trading days per stock — backfilling market data from NSE…"
        )

    def _sync_progress(current, total, message, _partial=None):
        if progress_callback:
            progress_callback(
                int(current),
                max(int(total) * 2, 1),
                f"Backfilling market data · {message}",
                None,
            )

    await sync_latest(progress_callback=_sync_progress)

    async with ui_session() as session:
        ready, _, _ = await count_symbols_ready_for_simulation(session, universe)
    return ready > 0


async def _run_backtest(
    progress_callback=None,
    step_delay_sec: float = 0.03,
    universe: str | None = None,
    force_refresh: bool = False,
):
    from datetime import date, timedelta

    import app.strategies.patterns  # noqa: F401
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent
    from app.services.backtest_loader import BacktestEngine
    from app.services.nifty_universe import DEFAULT_UNIVERSE, get_universe_config

    uni = (universe or DEFAULT_UNIVERSE).upper()
    cfg = get_universe_config(uni)

    from app.services.audit_types import InsufficientBacktestDataError

    try:
        async with audit_track(
            "backtest.run",
            AuditComponent.UI,
            universe=uni,
            force_refresh=force_refresh,
            stock_count=len(cfg["symbols"]),
        ):
            if not force_refresh:
                report, run_id, run_at = await _load_cached_simulation(uni)
                if report is not None:
                    return report, type("Run", (), {"id": run_id, "run_at": run_at})()

            if force_refresh:
                await _ensure_simulation_candle_data(uni, progress_callback)

            async with ui_session() as session:
                engine = BacktestEngine(universe=uni)
                report = await engine.run(
                    session,
                    progress_callback=progress_callback,
                    step_delay_sec=step_delay_sec,
                )
                if not report.patterns:
                    raise InsufficientBacktestDataError(
                        "Insufficient candle data for simulation — no patterns evaluated"
                    )
                run = await engine.persist(session, report)
                return report, run
    except InsufficientBacktestDataError:
        return None, None


async def _run_today_prediction(sync_first: bool = False, universe: str | None = None):
    """Predict latest closed day using OHLCV stored in the market-data table."""
    import app.strategies.patterns  # noqa: F401
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent
    from app.services.backtest_loader import BacktestEngine
    from app.services.nifty_universe import DEFAULT_UNIVERSE, get_universe_config

    uni = (universe or DEFAULT_UNIVERSE).upper()
    cfg = get_universe_config(uni)

    async with audit_track(
        "prediction.validate_today",
        AuditComponent.UI,
        universe=uni,
        sync_first=False,
    ):
        async with ui_session() as session:
            engine = BacktestEngine(universe=uni)
            return await engine.run_latest_prediction(session)


def partial_leaderboard(pattern_results: dict, stock_count: int = 15) -> pd.DataFrame:
    """Build a live ranking table from in-progress pattern results."""
    rows = []
    ranked = sorted(
        pattern_results.values(),
        key=lambda r: (r.avg_daily_score, r.overall_hit_rate),
        reverse=True,
    )
    for rank, pr in enumerate(ranked, start=1):
        if pr.total_signals == 0:
            continue
        rows.append(
            {
                "Rank": rank,
                "Pattern": pr.pattern_name,
                "Avg correct/day": f"{pr.avg_daily_score:.1f}/{stock_count}",
                "Hit rate %": round(pr.overall_hit_rate, 1),
                "Signals so far": pr.total_signals,
            }
        )
    return pd.DataFrame(rows)


async def _latest_backtest_run():
    from sqlalchemy.orm import selectinload

    from app.models import BacktestRun

    async with ui_session() as session:
        return await session.scalar(
            select(BacktestRun)
            .options(selectinload(BacktestRun.pattern_scores))
            .order_by(BacktestRun.run_at.desc())
            .limit(1)
        )


async def _backtest_pattern_detail(run_id: int, pattern_id: str):
    from app.api.routes.backtest import pattern_detail

    async with ui_session() as session:
        return await pattern_detail(run_id, pattern_id, session)


def list_registered_patterns():
    import app.strategies.patterns  # noqa: F401
    from app.strategies.registry import get_all_patterns

    return get_all_patterns()


def format_inr(value: Decimal | float | int | str | None) -> str:
    if value is None:
        return "—"
    num = float(value)
    if not math.isfinite(num):
        return "—"
    return f"₹{num:,.2f}"


def format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    if not math.isfinite(value):
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def format_ist_datetime(value: datetime | None, *, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a timezone-aware (or naive) timestamp in IST for display."""
    from app.services.market_calendar import IST

    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    else:
        value = value.astimezone(IST)
    return value.strftime(fmt)
