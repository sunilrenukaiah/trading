"""Serialize and load daily recommendation snapshots."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecommendationSnapshot
from app.defaults import DEFAULT_MAX_TARGET_PROFIT_PCT
from app.services.budget_allocator import AllocationLine, BudgetAllocationReport
from app.services.recommendation_engine import (
    PatternRanking,
    RecommendationReport,
    StockRecommendation,
    all_report_recommendations,
    apply_price_bucket_sanitize,
)
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.simulation_cache import today_ist

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MIDDAY_CACHE_PATH = DATA_DIR / "midday_recommendation_snapshot.json"


def _stock_from_dict(row: dict) -> StockRecommendation:
    from app.services.recommendation_engine import coerce_stock_recommendation

    return coerce_stock_recommendation(row)


def _ranking_from_dict(row: dict) -> PatternRanking:
    return PatternRanking(**row)


def serialize_snapshot(
    report: RecommendationReport,
    allocation: BudgetAllocationReport,
    *,
    budget_inr: float,
    max_target_profit_pct: float,
) -> dict:
    return {
        "report": {
            **asdict(report),
            "generated_at": report.generated_at.isoformat(),
            "prediction_date": report.prediction_date.isoformat(),
            "data_through_date": report.data_through_date.isoformat(),
            "top_patterns": [asdict(p) for p in report.top_patterns],
            "recommendations": [asdict(r) for r in report.recommendations],
            "price_bucket_recommendations": {
                label: [asdict(r) for r in recs]
                for label, recs in report.price_bucket_recommendations.items()
            },
        },
        "allocation": {
            **{k: v for k, v in asdict(allocation).items() if k != "lines"},
            "lines": [asdict(line) for line in allocation.lines],
        },
        "budget_inr": budget_inr,
        "max_target_profit_pct": max_target_profit_pct,
    }


def deserialize_snapshot(payload: dict) -> tuple[RecommendationReport, BudgetAllocationReport, float, float]:
    report_row = payload["report"]
    report = RecommendationReport(
        generated_at=date.fromisoformat(report_row["generated_at"]),
        prediction_date=date.fromisoformat(report_row["prediction_date"]),
        data_through_date=date.fromisoformat(report_row["data_through_date"]),
        lookback_days=report_row["lookback_days"],
        eval_days=report_row["eval_days"],
        top_patterns=[_ranking_from_dict(p) for p in report_row["top_patterns"]],
        recommendations=[_stock_from_dict(r) for r in report_row["recommendations"]],
        tier_counts=report_row.get("tier_counts", {}),
        max_target_profit_pct=float(
            report_row.get("max_target_profit_pct", DEFAULT_MAX_TARGET_PROFIT_PCT)
        ),
        notes=report_row.get("notes", []),
        price_bucket_recommendations={
            label: [_stock_from_dict(r) for r in recs]
            for label, recs in report_row.get("price_bucket_recommendations", {}).items()
        },
        price_bucket_counts=report_row.get("price_bucket_counts", {}),
    )
    apply_price_bucket_sanitize(report)
    from app.services.recommendation_engine import normalize_recommendation_report

    normalize_recommendation_report(report)

    alloc_row = payload["allocation"]
    allocation = BudgetAllocationReport(
        budget_inr=float(alloc_row["budget_inr"]),
        total_invested=float(alloc_row["total_invested"]),
        cash_remaining=float(alloc_row["cash_remaining"]),
        expected_profit=float(alloc_row["expected_profit"]),
        expected_return_pct=float(alloc_row["expected_return_pct"]),
        total_gross_profit=float(alloc_row["total_gross_profit"]),
        total_charges=float(alloc_row["total_charges"]),
        total_stcg_tax=float(alloc_row["total_stcg_tax"]),
        total_net_profit_after_tax=float(alloc_row["total_net_profit_after_tax"]),
        max_portfolio_loss=float(alloc_row["max_portfolio_loss"]),
        lines=[AllocationLine(**line) for line in alloc_row["lines"]],
    )
    budget = float(payload.get("budget_inr", allocation.budget_inr))
    max_target = float(payload.get("max_target_profit_pct", report.max_target_profit_pct))
    return report, allocation, budget, max_target


async def save_recommendation_snapshot(
    session: AsyncSession,
    report: RecommendationReport,
    allocation: BudgetAllocationReport,
    *,
    budget_inr: float,
    max_target_profit_pct: float,
    analysis_date: date | None = None,
) -> RecommendationSnapshot:
    day = analysis_date or today_ist()
    payload = serialize_snapshot(
        report,
        allocation,
        budget_inr=budget_inr,
        max_target_profit_pct=max_target_profit_pct,
    )

    existing_id = await session.scalar(
        select(RecommendationSnapshot.id).where(RecommendationSnapshot.analysis_date == day)
    )
    if existing_id:
        await session.execute(
            delete(RecommendationSnapshot).where(RecommendationSnapshot.id == existing_id)
        )

    row = RecommendationSnapshot(
        analysis_date=day,
        prediction_date=report.prediction_date,
        budget_inr=budget_inr,
        max_target_profit_pct=max_target_profit_pct,
        payload=payload,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def recommended_symbols_for_prediction_date(
    session: AsyncSession,
    prediction_date: date,
) -> set[str]:
    """Symbols in the recommendation pick list for a given prediction (trade) date."""
    row = await session.scalar(
        select(RecommendationSnapshot)
        .where(RecommendationSnapshot.prediction_date == prediction_date)
        .order_by(RecommendationSnapshot.generated_at.desc())
        .limit(1)
    )
    if row is None:
        recent = (
            await session.scalars(
                select(RecommendationSnapshot)
                .order_by(RecommendationSnapshot.generated_at.desc())
                .limit(30)
            )
        ).all()
        for candidate in recent:
            payload = candidate.payload
            if isinstance(payload, str):
                payload = json.loads(payload)
            report, _, _, _ = deserialize_snapshot(payload)
            if report.prediction_date == prediction_date:
                row = candidate
                break
    if row is None:
        return set()

    payload = row.payload
    if isinstance(payload, str):
        payload = json.loads(payload)
    report, _, _, _ = deserialize_snapshot(payload)
    return {rec.symbol.upper() for rec in all_report_recommendations(report)}


async def load_cached_recommendations_for_ui() -> (
    tuple[RecommendationReport, BudgetAllocationReport, float, float, datetime] | None
):
    """Prefer today's analysis run; fall back to prediction-date match only if needed."""
    from app.services.market_calendar import active_market_session_date

    cached = await load_cached_recommendations(today_ist())
    if cached is not None:
        return cached
    return await load_cached_recommendations(prediction_date=active_market_session_date())


async def load_cached_recommendations(
    analysis_date: date | None = None,
    *,
    prediction_date: date | None = None,
) -> tuple[RecommendationReport, BudgetAllocationReport, float, float, datetime] | None:
    from app.db.ui_session import ui_session

    day = analysis_date or today_ist()
    try:
        async with ui_session() as session:
            query = select(RecommendationSnapshot).order_by(
                RecommendationSnapshot.generated_at.desc()
            )
            if prediction_date is not None:
                query = query.where(RecommendationSnapshot.prediction_date == prediction_date)
            else:
                query = query.where(RecommendationSnapshot.analysis_date == day)
            row = await session.scalar(query.limit(1))
            if row is None:
                return None
            payload = row.payload
            if isinstance(payload, str):
                payload = json.loads(payload)
            report, allocation, budget, max_target = deserialize_snapshot(payload)
            return report, allocation, budget, max_target, row.generated_at
    except Exception:
        return None


def save_midday_recommendation_snapshot(
    report: RecommendationReport,
    allocation: BudgetAllocationReport,
    *,
    budget_inr: float,
    max_target_profit_pct: float,
    analysis_date: date | None = None,
) -> datetime:
    """Persist today's mid-day run to a local JSON file (separate from morning DB snapshot)."""
    day = analysis_date or today_ist()
    generated_at = datetime.now(IST)
    payload = serialize_snapshot(
        report,
        allocation,
        budget_inr=budget_inr,
        max_target_profit_pct=max_target_profit_pct,
    )
    doc = {
        "analysis_date": day.isoformat(),
        "generated_at": generated_at.isoformat(),
        "payload": payload,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MIDDAY_CACHE_PATH.write_text(json.dumps(doc, indent=2))
    return generated_at


def load_midday_cached_recommendations(
    analysis_date: date | None = None,
) -> tuple[RecommendationReport, BudgetAllocationReport, float, float, datetime] | None:
    day = analysis_date or today_ist()
    if not MIDDAY_CACHE_PATH.is_file():
        return None
    try:
        raw = json.loads(MIDDAY_CACHE_PATH.read_text())
        if date.fromisoformat(str(raw["analysis_date"])) != day:
            return None
        generated_at = datetime.fromisoformat(str(raw["generated_at"]))
        report, allocation, budget, max_target = deserialize_snapshot(raw["payload"])
        return report, allocation, budget, max_target, generated_at
    except Exception:
        return None


def load_midday_cached_recommendations_for_ui() -> (
    tuple[RecommendationReport, BudgetAllocationReport, float, float, datetime] | None
):
    """Load today's mid-day analysis run, if one was saved."""
    return load_midday_cached_recommendations(today_ist())


async def find_recommendation_for_symbol(
    session: AsyncSession,
    symbol: str,
) -> tuple[StockRecommendation | None, AllocationLine | None]:
    """Search recent recommendation snapshots for a symbol (any analysis date)."""
    from sqlalchemy import desc

    sym = symbol.upper()
    rows = (
        await session.scalars(
            select(RecommendationSnapshot).order_by(desc(RecommendationSnapshot.generated_at)).limit(20)
        )
    ).all()

    for row in rows:
        payload = row.payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        report, allocation, _, _ = deserialize_snapshot(payload)
        for rec in all_report_recommendations(report):
            if rec.symbol.upper() == sym:
                line = next((ln for ln in allocation.lines if ln.symbol.upper() == sym), None)
                return rec, line
        for line in allocation.lines:
            if line.symbol.upper() == sym:
                stub = next((r for r in all_report_recommendations(report) if r.symbol.upper() == sym), None)
                return stub, line
    return None, None
