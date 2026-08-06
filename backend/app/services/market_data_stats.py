"""Market data and simulation snapshot statistics for the UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.ui_session import ui_session
from app.models import BacktestRun, OhlcvCandle, RecommendationSnapshot
from app.services.simulation_cache import deserialize_report, today_ist

__all__ = [
    "MarketDataStats",
    "fetch_market_data_stats",
    "get_market_data_stats",
]


@dataclass
class MarketDataStats:
    stocks_with_data: int
    earliest_date: date | None
    latest_date: date | None
    simulation_universe: str | None
    simulation_date: date | None
    simulation_saved_at: datetime | None
    simulation_from_cache: bool
    top_patterns: list[tuple[str, float]]
    recommendation_eval_days: int | None = None


def _top_patterns_from_snapshot_payload(payload: dict) -> list[tuple[str, float]]:
    top = payload.get("report", {}).get("top_patterns", [])
    return [(str(p["pattern_name"]), float(p["hit_rate_pct"])) for p in top]


def _top_patterns_from_backtest_payload(
    payload: dict,
    *,
    min_hit_rate: float,
    top_n: int,
    min_signals: int = 5,
) -> list[tuple[str, float]]:
    from app.services.recommendation_engine import (
        PatternRanking,
        _ranking_sort_key,
        select_top_patterns,
    )

    report = deserialize_report(payload)
    rankings: list[PatternRanking] = []
    for pr in report.patterns:
        if pr.total_signals < min_signals:
            continue
        rankings.append(
            PatternRanking(
                pattern_id=pr.pattern_id,
                pattern_name=pr.pattern_name,
                hit_rate_pct=round(pr.overall_hit_rate, 1),
                total_correct=pr.total_correct,
                total_signals=pr.total_signals,
                avg_daily_score=round(pr.avg_daily_score, 2),
            )
        )
    rankings.sort(key=_ranking_sort_key, reverse=True)
    qualified = select_top_patterns(rankings, min_hit_rate=min_hit_rate, top_n=top_n)
    return [(p.pattern_name, p.hit_rate_pct) for p in qualified]


async def _load_cached_top_patterns(
    session: AsyncSession,
    *,
    universe: str,
    backtest_run: BacktestRun | None,
) -> tuple[list[tuple[str, float]], int | None]:
    """Reuse top patterns from the latest recommendation snapshot or backtest cache."""
    from app.services.recommendation_engine import universe_config

    rec_cfg = universe_config()
    eval_days = int(rec_cfg.get("eval_days", 15))
    min_hit_rate = float(rec_cfg.get("min_pattern_hit_rate_pct", 55))
    top_n = int(rec_cfg.get("top_patterns_count", 3))

    snap = await session.scalar(
        select(RecommendationSnapshot)
        .order_by(RecommendationSnapshot.generated_at.desc())
        .limit(1)
    )
    if snap is not None:
        payload = snap.payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        top_patterns = _top_patterns_from_snapshot_payload(payload)
        if top_patterns:
            return top_patterns[:top_n], eval_days

    if backtest_run and backtest_run.report_payload:
        payload = backtest_run.report_payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        if str(payload.get("universe", universe)).upper() == universe.upper():
            top_patterns = _top_patterns_from_backtest_payload(
                payload,
                min_hit_rate=min_hit_rate,
                top_n=top_n,
            )
            if top_patterns:
                return top_patterns, eval_days

    return [], eval_days


async def get_market_data_stats(
    universe: str = "NIFTY250",
    *,
    session: AsyncSession | None = None,
) -> MarketDataStats:
    uni = universe.upper()
    if session is not None:
        return await fetch_market_data_stats(session, uni)

    async with ui_session() as owned:
        return await fetch_market_data_stats(owned, uni)


async def fetch_market_data_stats(session: AsyncSession, universe: str = "NIFTY250") -> MarketDataStats:
    """Load stats using an existing async session (Streamlit bundled page load)."""
    uni = universe.upper()
    today = today_ist()

    stocks_with_data = int(
        await session.scalar(select(func.count(func.distinct(OhlcvCandle.instrument_id)))) or 0
    )
    earliest = await session.scalar(select(func.min(OhlcvCandle.trade_date)))
    latest = await session.scalar(select(func.max(OhlcvCandle.trade_date)))

    run = await session.scalar(
        select(BacktestRun)
        .where(
            BacktestRun.universe == uni,
            BacktestRun.simulation_date == today,
            BacktestRun.report_payload.isnot(None),
        )
        .order_by(BacktestRun.run_at.desc())
        .limit(1)
    )
    if run is None:
        run = await session.scalar(
            select(BacktestRun)
            .where(
                BacktestRun.universe == uni,
                BacktestRun.report_payload.isnot(None),
            )
            .order_by(BacktestRun.run_at.desc())
            .limit(1)
        )

    top_patterns: list[tuple[str, float]] = []
    recommendation_eval_days: int | None = None
    sim_date = None
    sim_saved_at = None
    from_cache = False

    if run and run.report_payload:
        sim_date = run.simulation_date
        sim_saved_at = run.run_at
        from_cache = run.simulation_date == today

    elif run:
        sim_date = run.simulation_date
        sim_saved_at = run.run_at
        from_cache = run.simulation_date == today

    try:
        top_patterns, recommendation_eval_days = await _load_cached_top_patterns(
            session,
            universe=uni,
            backtest_run=run,
        )
    except Exception:
        top_patterns = []
        recommendation_eval_days = None

    return MarketDataStats(
        stocks_with_data=stocks_with_data,
        earliest_date=earliest,
        latest_date=latest,
        simulation_universe=uni if run else None,
        simulation_date=sim_date,
        simulation_saved_at=sim_saved_at,
        simulation_from_cache=from_cache,
        top_patterns=top_patterns,
        recommendation_eval_days=recommendation_eval_days,
    )


# Backward-compatible alias (do not import from ui code — use fetch_market_data_stats).
_fetch_market_data_stats = fetch_market_data_stats
