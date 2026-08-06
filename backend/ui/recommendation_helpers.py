"""Recommendation engine helpers for Streamlit."""

from __future__ import annotations

from app.db.ui_session import ui_session
from app.services.budget_allocator import allocate_budget
from app.services.recommendation_engine import (
    CAP_TIERS,
    _tier_display,
    load_market_universe_candles_from_db,
    market_universe_symbol_set,
    partition_symbol_data_by_tier,
    refresh_market_universe_symbol_set,
    run_recommendation_engine,
    universe_config,
)


def _ensure_analysis_modules_fresh() -> None:
    """Refresh nifty_universe exports without purging this module mid-import."""
    from ui.streamlit_imports import ensure_nifty_universe_fresh

    ensure_nifty_universe_fresh()


async def load_tier_symbol_data(progress_callback=None) -> dict[str, dict[str, pd.DataFrame]]:
    """Load NIFTY250 OHLCV from DB, partitioned into large / mid / small cap by latest close."""
    allowed = market_universe_symbol_set()
    async with ui_session() as session:
        nifty_data = await load_market_universe_candles_from_db(
            session, min_rows=25, allowed=allowed
        )
    by_tier = partition_symbol_data_by_tier(nifty_data)

    total = sum(len(symbols) for symbols in by_tier.values())
    loaded = 0
    for tier in CAP_TIERS:
        for symbol in by_tier.get(tier, {}):
            loaded += 1
            if progress_callback:
                progress_callback(
                    loaded,
                    max(total, 1),
                    f"Loaded {symbol} · {_tier_display(tier)} ({loaded}/{total})",
                    None,
                )

    return by_tier


def _recommendation_min_candle_rows() -> int:
    from app.services.backtest import min_candles_for_simulation

    cfg = universe_config()
    return min_candles_for_simulation(
        int(cfg.get("lookback_days", 30)),
        int(cfg.get("eval_days", 30)),
    )


async def run_recommendation_analysis(
    budget_inr: float,
    progress_callback=None,
    *,
    max_target_profit_pct: float | None = None,
):
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent

    _ensure_analysis_modules_fresh()
    from app.services.nifty_universe import is_universe_cache_fresh

    async with audit_track(
        "recommendation.run",
        AuditComponent.UI,
        budget_inr=budget_inr,
        max_target_profit_pct=max_target_profit_pct,
    ):
        if progress_callback:
            if is_universe_cache_fresh():
                progress_callback("Loading NIFTY250 constituent list (cached today)…")
            else:
                progress_callback("Refreshing NIFTY250 constituent list from NSE…")

        allowed = refresh_market_universe_symbol_set()

        if progress_callback:
            progress_callback("Loading NIFTY250 market data from database…")

        min_rows = _recommendation_min_candle_rows()
        async with ui_session() as session:
            nifty_data = await load_market_universe_candles_from_db(
                session, min_rows=min_rows, allowed=allowed
            )

        tier_data = partition_symbol_data_by_tier(nifty_data)

        total = len(nifty_data)
        if progress_callback and total:
            progress_callback(8, 100, f"Loaded {total} NIFTY250 symbols · ranking patterns…", None)
        elif progress_callback:
            cfg = universe_config()
            progress_callback(
                8,
                100,
                f"Ranking patterns ({cfg.get('eval_days', 30)}-day backtest)…",
                None,
            )

        report = run_recommendation_engine(
            tier_data,
            ranking_data_by_tier=tier_data,
            bucket_symbol_data=nifty_data,
            max_target_profit_pct=max_target_profit_pct,
            progress_callback=progress_callback,
        )

        if progress_callback:
            progress_callback(96, 100, "Allocating budget…", None)

        cfg = universe_config()
        allocation = allocate_budget(
            report,
            budget_inr,
            tier_budget_split_pct=cfg.get("tier_budget_split_pct", 33.33),
        )

        from app.services.recommendation_cache import save_recommendation_snapshot

        if progress_callback:
            progress_callback(98, 100, "Saving recommendations…", None)

        async with ui_session() as session:
            await save_recommendation_snapshot(
                session,
                report,
                allocation,
                budget_inr=budget_inr,
                max_target_profit_pct=max_target_profit_pct or report.max_target_profit_pct,
            )

        return report, allocation


async def run_midday_recommendation_analysis(
    budget_inr: float,
    progress_callback=None,
    *,
    max_target_profit_pct: float | None = None,
):
    """
    Mid-day scan: upsert partial session OHLC, rerun engine, allocate budget.

    Does not overwrite the morning recommendation snapshot in the database.
    """
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent
    from app.services.market_calendar import is_midday_analysis_ready
    from app.services.midday_market_sync import upsert_intraday_session_candles

    _ensure_analysis_modules_fresh()

    if not is_midday_analysis_ready():
        raise ValueError(
            "Mid-day analysis is only available from 11:45 AM to 4:30 PM IST on trading days."
        )

    async with audit_track(
        "recommendation.midday_run",
        AuditComponent.UI,
        budget_inr=budget_inr,
        max_target_profit_pct=max_target_profit_pct,
    ):
        if progress_callback:
            progress_callback(0, 100, "Updating session OHLC for NIFTY250…", None)

        async with ui_session() as session:
            sync_stats = await upsert_intraday_session_candles(
                session,
                progress_callback=lambda i, total, msg: progress_callback(
                    int(i * 40 / max(total, 1)),
                    100,
                    msg,
                    None,
                )
                if progress_callback
                else None,
            )

        from app.services.app_logger import get_logger

        log = get_logger(__name__)
        log.info(
            "Mid-day recommendation analysis starting budget_inr=%s sync=%s",
            budget_inr,
            sync_stats,
        )

        if progress_callback:
            progress_callback(42, 100, "Loading NIFTY250 constituent list…", None)

        allowed = refresh_market_universe_symbol_set()

        if progress_callback:
            progress_callback(45, 100, "Loading updated market data from database…", None)

        min_rows = _recommendation_min_candle_rows()
        async with ui_session() as session:
            nifty_data = await load_market_universe_candles_from_db(
                session, min_rows=min_rows, allowed=allowed
            )

        tier_data = partition_symbol_data_by_tier(nifty_data)

        if progress_callback:
            cfg = universe_config()
            progress_callback(
                50,
                100,
                f"Ranking patterns ({cfg.get('eval_days', 30)}-day backtest)…",
                None,
            )

        report = run_recommendation_engine(
            tier_data,
            ranking_data_by_tier=tier_data,
            bucket_symbol_data=nifty_data,
            max_target_profit_pct=max_target_profit_pct,
            progress_callback=progress_callback,
        )
        report.notes = list(report.notes) + [
            f"Mid-day analysis — session OHLC through {sync_stats.get('trade_date')} "
            f"({sync_stats.get('candles_upserted', 0)} updated, "
            f"{sync_stats.get('symbols_fresh_skipped', 0)} fresh skipped)."
        ]

        if progress_callback:
            progress_callback(96, 100, "Allocating budget…", None)

        cfg = universe_config()
        allocation = allocate_budget(
            report,
            budget_inr,
            tier_budget_split_pct=cfg.get("tier_budget_split_pct", 33.33),
        )

        from app.services.recommendation_cache import save_midday_recommendation_snapshot

        if progress_callback:
            progress_callback(98, 100, "Saving mid-day analysis…", None)

        save_midday_recommendation_snapshot(
            report,
            allocation,
            budget_inr=budget_inr,
            max_target_profit_pct=max_target_profit_pct or report.max_target_profit_pct,
        )

        log.info(
            "Mid-day recommendation analysis finished prediction_date=%s picks=%s",
            report.prediction_date,
            len(allocation.lines),
        )

        return report, allocation


# Backward-compatible alias used by dashboard
_run_recommendation_engine = run_recommendation_analysis
