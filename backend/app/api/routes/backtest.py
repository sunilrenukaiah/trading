from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models import BacktestPatternScore, BacktestRun
from app.schemas.backtest import BacktestRunOut, DayDetailOut, PatternInfo, PatternScoreOut, StockScoreOut
from app.services.backtest import BacktestEngine
from app.strategies.registry import get_all_patterns, get_pattern

import app.strategies.patterns  # noqa: F401

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("/patterns", response_model=list[PatternInfo])
async def list_patterns():
    return [
        PatternInfo(id=p.id, name=p.name, lookback_days=p.lookback_days)
        for p in get_all_patterns()
    ]


@router.post("/run", response_model=BacktestRunOut)
async def run_backtest(db: AsyncSession = Depends(get_db)):
    from app.services.audit import audit_track
    from app.services.audit_types import AuditComponent

    async with audit_track("backtest.api_run", AuditComponent.API):
        engine = BacktestEngine()
        report = await engine.run(db)
        if not report.patterns:
            raise HTTPException(
                status_code=400,
                detail="Insufficient candle data. Run POST /api/admin/sync first.",
            )
        run = await engine.persist(db, report)
        return _run_to_schema(run)


@router.get("/latest", response_model=BacktestRunOut | None)
async def latest_backtest(db: AsyncSession = Depends(get_db)):
    run = await db.scalar(
        select(BacktestRun)
        .options(selectinload(BacktestRun.pattern_scores))
        .order_by(BacktestRun.run_at.desc())
        .limit(1)
    )
    if not run:
        return None
    return _run_to_schema(run)


@router.get("/{run_id}", response_model=BacktestRunOut)
async def get_backtest(run_id: int, db: AsyncSession = Depends(get_db)):
    run = await db.scalar(
        select(BacktestRun)
        .where(BacktestRun.id == run_id)
        .options(selectinload(BacktestRun.pattern_scores))
    )
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")
    return _run_to_schema(run)


@router.get("/{run_id}/patterns/{pattern_id}/detail")
async def pattern_detail(run_id: int, pattern_id: str, db: AsyncSession = Depends(get_db)):
    """Re-run pattern evaluation to produce per-day stock breakdown."""
    from app.models import BacktestStockScore

    run = await db.get(BacktestRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Backtest run not found")

    stock_scores = (
        await db.scalars(
            select(BacktestStockScore).where(
                BacktestStockScore.run_id == run_id,
                BacktestStockScore.pattern_id == pattern_id,
            )
        )
    ).all()

    pattern = get_pattern(pattern_id)
    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")

    engine = BacktestEngine(
        lookback_days=run.lookback_days,
        eval_days=run.eval_days,
        universe=run.universe,
    )
    symbol_data = {}
    for symbol in engine.symbols:
        df = await engine.load_symbol_candles(db, symbol)
        if df is not None:
            symbol_data[symbol] = df

    report = engine.run_on_data(symbol_data)
    pr = next((p for p in report.patterns if p.pattern_id == pattern_id), None)
    if not pr:
        raise HTTPException(status_code=404, detail="Pattern result not found")

    return {
        "pattern_id": pattern_id,
        "pattern_name": pattern.name,
        "stock_scores": [StockScoreOut.model_validate(s) for s in stock_scores],
        "day_details": [_day_detail_to_schema(d) for d in pr.day_details],
    }


def _day_detail_to_schema(d) -> DayDetailOut:
    prev = d.prev_close
    pred = d.predicted_close
    act = d.actual_close
    pred_chg = ((pred - prev) / prev * 100) if prev else 0.0
    act_chg = ((act - prev) / prev * 100) if prev else 0.0
    price_err = ((act - pred) / pred * 100) if pred else 0.0
    return DayDetailOut(
        trade_date=d.trade_date.isoformat(),
        symbol=d.symbol,
        signal=d.signal.value,
        actual=d.actual.value,
        correct=d.correct,
        prev_close=round(prev, 4),
        predicted_close=round(pred, 4),
        actual_close=round(act, 4),
        predicted_change_pct=round(pred_chg, 2),
        actual_change_pct=round(act_chg, 2),
        price_error_pct=round(price_err, 2),
    )


def _run_to_schema(run: BacktestRun) -> BacktestRunOut:
    patterns = sorted(run.pattern_scores, key=lambda p: p.rank)
    return BacktestRunOut(
        id=run.id,
        run_at=run.run_at,
        eval_days=run.eval_days,
        lookback_days=run.lookback_days,
        stock_count=run.stock_count,
        patterns=[
            PatternScoreOut(
                pattern_id=p.pattern_id,
                pattern_name=p.pattern_name,
                total_correct=p.total_correct,
                total_signals=p.total_signals,
                avg_daily_score=p.avg_daily_score,
                overall_hit_rate=p.overall_hit_rate,
                rank=p.rank,
                avg_display=f"{p.avg_daily_score}/15",
            )
            for p in patterns
        ],
    )
