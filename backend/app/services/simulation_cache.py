"""Serialize and load daily backtest simulation snapshots."""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import BacktestPatternScore, BacktestRun, BacktestStockScore
from app.services.backtest import BacktestReport, DayResult, PatternResult
from app.services.nifty_universe import DEFAULT_UNIVERSE
from app.strategies.base import Signal

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    return datetime.now(IST).date()


def serialize_report(report: BacktestReport) -> dict:
    return {
        "eval_days": report.eval_days,
        "lookback_days": report.lookback_days,
        "stock_count": report.stock_count,
        "universe": report.universe,
        "symbols": report.symbols,
        "patterns": [
            {
                "pattern_id": pr.pattern_id,
                "pattern_name": pr.pattern_name,
                "total_correct": pr.total_correct,
                "total_signals": pr.total_signals,
                "daily_scores": pr.daily_scores,
                "stock_correct": pr.stock_correct,
                "stock_signals": pr.stock_signals,
                "day_details": [
                    {
                        "trade_date": d.trade_date.isoformat(),
                        "symbol": d.symbol,
                        "signal": d.signal.value,
                        "actual": d.actual.value,
                        "correct": d.correct,
                        "prev_close": d.prev_close,
                        "predicted_close": d.predicted_close,
                        "actual_close": d.actual_close,
                    }
                    for d in pr.day_details
                ],
            }
            for pr in report.patterns
        ],
    }


def deserialize_report(payload: dict) -> BacktestReport:
    patterns: list[PatternResult] = []
    for row in payload.get("patterns", []):
        day_details = [
            DayResult(
                trade_date=date.fromisoformat(d["trade_date"]),
                symbol=d["symbol"],
                signal=Signal(d["signal"]),
                actual=Signal(d["actual"]),
                correct=d["correct"],
                prev_close=d["prev_close"],
                predicted_close=d["predicted_close"],
                actual_close=d["actual_close"],
            )
            for d in row.get("day_details", [])
        ]
        patterns.append(
            PatternResult(
                pattern_id=row["pattern_id"],
                pattern_name=row["pattern_name"],
                total_correct=row["total_correct"],
                total_signals=row["total_signals"],
                daily_scores=row.get("daily_scores", []),
                stock_correct=row.get("stock_correct", {}),
                stock_signals=row.get("stock_signals", {}),
                day_details=day_details,
            )
        )
    return BacktestReport(
        eval_days=payload["eval_days"],
        lookback_days=payload["lookback_days"],
        stock_count=payload["stock_count"],
        patterns=patterns,
        universe=payload.get("universe", DEFAULT_UNIVERSE),
        symbols=payload.get("symbols", []),
    )


async def get_daily_simulation(
    session: AsyncSession,
    universe: str,
    simulation_date: date | None = None,
) -> tuple[BacktestReport, BacktestRun] | None:
    sim_date = simulation_date or today_ist()
    uni = universe.upper()

    run = await session.scalar(
        select(BacktestRun)
        .where(
            BacktestRun.simulation_date == sim_date,
            BacktestRun.universe == uni,
        )
        .options(selectinload(BacktestRun.pattern_scores))
        .limit(1)
    )
    if not run or not run.report_payload:
        return None

    payload = run.report_payload
    if isinstance(payload, str):
        payload = json.loads(payload)

    return deserialize_report(payload), run


async def load_cached_simulation(
    universe: str,
) -> tuple[BacktestReport | None, int | None, datetime | None]:
    """Load today's cached simulation for a universe, if stored."""
    from app.db.ui_session import ui_session

    try:
        async with ui_session() as session:
            result = await get_daily_simulation(session, universe, today_ist())
            if result:
                report, run = result
                return report, run.id, run.run_at
    except Exception:
        pass
    return None, None, None


async def save_daily_simulation(
    session: AsyncSession,
    report: BacktestReport,
    run: BacktestRun,
    simulation_date: date | None = None,
) -> BacktestRun:
    sim_date = simulation_date or today_ist()
    uni = report.universe.upper()

    existing = await session.scalar(
        select(BacktestRun.id).where(
            BacktestRun.simulation_date == sim_date,
            BacktestRun.universe == uni,
        )
    )
    if existing and existing != run.id:
        await session.execute(delete(BacktestRun).where(BacktestRun.id == existing))

    run.universe = uni
    run.simulation_date = sim_date
    run.report_payload = serialize_report(report)
    await session.commit()
    await session.refresh(run)
    return run
