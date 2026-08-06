"""Backtest engine — same-day pattern validation on historical OHLCV."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.strategies.patterns  # noqa: F401 — register all patterns
from app.services.app_logger import get_logger
from app.services.nifty_universe import DEFAULT_UNIVERSE, get_universe_config
from app.strategies.base import Signal
from app.strategies.registry import get_all_patterns

UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "data" / "backtest_universe.json"

log = get_logger(__name__)


def _orm_models():
    """Resolve ORM classes on each call — safe after Streamlit reloads app.models."""
    from app.models import (
        BacktestPatternScore,
        BacktestRun,
        BacktestStockScore,
        Instrument,
        OhlcvCandle,
    )

    return Instrument, OhlcvCandle, BacktestRun, BacktestPatternScore, BacktestStockScore


@dataclass
class DayResult:
    trade_date: date
    symbol: str
    signal: Signal
    actual: Signal
    correct: bool
    prev_close: float
    predicted_close: float
    actual_close: float


def _predicted_close(signal: Signal, prev_close: float, lookback_df: pd.DataFrame) -> float:
    """Project a target close from recent average daily move in the signal direction."""
    changes = lookback_df["close"].astype(float).diff().dropna()
    avg_move = float(changes.mean()) if len(changes) else 0.0
    magnitude = abs(avg_move) if avg_move != 0 else prev_close * 0.005
    if signal == Signal.BULLISH:
        return round(prev_close + magnitude, 4)
    if signal == Signal.BEARISH:
        return round(prev_close - magnitude, 4)
    return round(prev_close, 4)


@dataclass
class PatternResult:
    pattern_id: str
    pattern_name: str
    total_correct: int = 0
    total_signals: int = 0
    daily_scores: list[float] = field(default_factory=list)
    stock_correct: dict[str, int] = field(default_factory=dict)
    stock_signals: dict[str, int] = field(default_factory=dict)
    day_details: list[DayResult] = field(default_factory=list)

    @property
    def overall_hit_rate(self) -> float:
        if self.total_signals == 0:
            return 0.0
        return self.total_correct / self.total_signals * 100

    @property
    def avg_daily_score(self) -> float:
        if not self.daily_scores:
            return 0.0
        return sum(self.daily_scores) / len(self.daily_scores)


@dataclass
class BacktestReport:
    eval_days: int
    lookback_days: int
    stock_count: int
    patterns: list[PatternResult]
    universe: str = "NIFTY250"
    symbols: list[str] = field(default_factory=list)


@dataclass
class LatestPredictionReport:
    """Single-day forecast: patterns evaluated on lookback through data_through_date, scored on prediction_date."""

    prediction_date: date
    data_through_date: date
    lookback_days: int
    stock_count: int
    patterns: list[PatternResult]
    universe: str = "NIFTY250"
    symbols: list[str] = field(default_factory=list)


ProgressCallback = Callable[[int, int, str, dict[str, PatternResult] | None], None]


def min_candles_for_simulation(lookback_days: int, eval_days: int) -> int:
    """Minimum daily bars required per symbol for the 30-day simulation."""
    return lookback_days + eval_days + 1


def required_backfill_calendar_days(
    lookback_days: int,
    eval_days: int,
    *,
    holiday_buffer: int = 25,
) -> int:
    """Calendar days of history needed to cover the simulation window (~252 trading days/year)."""
    min_trading = min_candles_for_simulation(lookback_days, eval_days)
    return int(min_trading * 365 / 252) + holiday_buffer


async def count_symbols_ready_for_simulation(
    session: AsyncSession,
    universe: str,
) -> tuple[int, int, int]:
    """Return (symbols_with_enough_bars, total_symbols, min_bars_required)."""
    engine = BacktestEngine(universe=universe)
    min_bars = min_candles_for_simulation(engine.lookback_days, engine.eval_days)
    ready = 0
    for symbol in engine.symbols:
        if await engine.load_symbol_candles(session, symbol) is not None:
            ready += 1
    return ready, len(engine.symbols), min_bars


def _actual_signal(prev_close: float, curr_close: float) -> Signal:
    if curr_close > prev_close:
        return Signal.BULLISH
    if curr_close < prev_close:
        return Signal.BEARISH
    return Signal.NEUTRAL


def _candles_to_df(rows: list) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_date": r.trade_date,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": int(r.volume),
            }
            for r in rows
        ]
    ).sort_values("trade_date")


class BacktestEngine:
    def __init__(
        self,
        lookback_days: int | None = None,
        eval_days: int | None = None,
        symbols: list[str] | None = None,
        universe: str | None = None,
    ):
        uni = (universe or DEFAULT_UNIVERSE).upper()
        cfg = get_universe_config(uni)
        self.universe = uni
        self.lookback_days = lookback_days or cfg["lookback_days"]
        self.eval_days = eval_days or cfg["eval_days"]
        self.symbols = symbols or cfg["symbols"]
        self.patterns = get_all_patterns()

    async def load_symbol_candles(
        self, session: AsyncSession, symbol: str
    ) -> pd.DataFrame | None:
        Instrument, OhlcvCandle, *_ = _orm_models()
        instrument = await session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        if not instrument:
            return None
        rows = (
            await session.scalars(
                select(OhlcvCandle)
                .where(OhlcvCandle.instrument_id == instrument.id)
                .order_by(OhlcvCandle.trade_date.asc())
            )
        ).all()
        if len(rows) < self.lookback_days + self.eval_days + 1:
            return None
        return _candles_to_df(rows)

    async def load_symbol_candles_for_prediction(
        self, session: AsyncSession, symbol: str
    ) -> pd.DataFrame | None:
        """Minimum history for a single-day forecast (lookback + prediction day)."""
        Instrument, OhlcvCandle, *_ = _orm_models()
        instrument = await session.scalar(
            select(Instrument).where(Instrument.symbol == symbol.upper())
        )
        if not instrument:
            return None
        rows = (
            await session.scalars(
                select(OhlcvCandle)
                .where(OhlcvCandle.instrument_id == instrument.id)
                .order_by(OhlcvCandle.trade_date.asc())
            )
        ).all()
        if len(rows) < self.lookback_days + 1:
            return None
        return _candles_to_df(rows)

    @staticmethod
    def _common_dates(symbol_data: dict[str, pd.DataFrame]) -> list[date]:
        common: set[date] | None = None
        for df in symbol_data.values():
            dates = set(df["trade_date"].tolist())
            common = dates if common is None else common & dates
        return sorted(common) if common else []

    def _evaluate_day(
        self,
        symbol_data: dict[str, pd.DataFrame],
        eval_date: date,
        pattern_results: dict[str, PatternResult],
    ) -> date | None:
        """Run all patterns for one eval date; lookback ends the prior trading day."""
        data_through: date | None = None
        symbols = list(symbol_data.keys())

        for symbol in symbols:
            df = symbol_data[symbol]
            idx_list = df.index[df["trade_date"] == eval_date].tolist()
            if not idx_list:
                continue
            idx = idx_list[0]
            pos = df.index.get_loc(idx)
            if pos < self.lookback_days:
                continue

            lookback_df = df.iloc[pos - self.lookback_days : pos].copy()
            prev_close = float(df.iloc[pos - 1]["close"])
            curr_close = float(df.iloc[pos]["close"])
            through = df.iloc[pos - 1]["trade_date"]
            if isinstance(through, pd.Timestamp):
                through = through.date()
            data_through = through if data_through is None else min(data_through, through)

            actual = _actual_signal(prev_close, curr_close)
            if actual == Signal.NEUTRAL:
                continue

            for pattern in self.patterns:
                try:
                    signal = pattern.evaluate(lookback_df)
                except Exception:
                    continue
                if signal == Signal.NEUTRAL:
                    continue

                pr = pattern_results[pattern.id]
                correct = signal == actual
                pr.total_signals += 1
                pr.stock_signals[symbol] = pr.stock_signals.get(symbol, 0) + 1
                if correct:
                    pr.total_correct += 1
                    pr.stock_correct[symbol] = pr.stock_correct.get(symbol, 0) + 1
                pr.day_details.append(
                    DayResult(
                        trade_date=eval_date,
                        symbol=symbol,
                        signal=signal,
                        actual=actual,
                        correct=correct,
                        prev_close=prev_close,
                        predicted_close=_predicted_close(signal, prev_close, lookback_df),
                        actual_close=curr_close,
                    )
                )

        return data_through

    def run_latest_prediction_on_data(
        self, symbol_data: dict[str, pd.DataFrame]
    ) -> LatestPredictionReport | None:
        """
        Predict the latest common trading day using only prior candles for pattern evaluation.
        Example: with data through 29 Jul, patterns use lookback ending 28 Jul and are scored on 29 Jul close.
        """
        sorted_dates = self._common_dates(symbol_data)
        if not sorted_dates:
            return None

        prediction_date = sorted_dates[-1]
        pattern_results: dict[str, PatternResult] = {
            p.id: PatternResult(pattern_id=p.id, pattern_name=p.name) for p in self.patterns
        }
        data_through = self._evaluate_day(symbol_data, prediction_date, pattern_results)
        if data_through is None:
            return None

        ranked = sorted(
            pattern_results.values(),
            key=lambda r: (r.total_correct, r.overall_hit_rate),
            reverse=True,
        )
        return LatestPredictionReport(
            prediction_date=prediction_date,
            data_through_date=data_through,
            lookback_days=self.lookback_days,
            stock_count=len(symbol_data),
            patterns=ranked,
            universe=self.universe,
            symbols=list(symbol_data.keys()),
        )

    def run_on_data(
        self,
        symbol_data: dict[str, pd.DataFrame],
        progress_callback: ProgressCallback | None = None,
        step_delay_sec: float = 0.0,
        *,
        count_signal: Signal | None = None,
    ) -> BacktestReport:
        pattern_results: dict[str, PatternResult] = {
            p.id: PatternResult(pattern_id=p.id, pattern_name=p.name) for p in self.patterns
        }

        sorted_dates = self._common_dates(symbol_data)
        if not sorted_dates:
            return BacktestReport(
                self.eval_days,
                self.lookback_days,
                len(symbol_data),
                [],
                universe=self.universe,
                symbols=list(symbol_data.keys()),
            )

        eval_dates = sorted_dates[-self.eval_days :]
        symbols = list(symbol_data.keys())
        total_steps = len(eval_dates) * len(symbols)
        step = 0

        for eval_date in eval_dates:
            day_correct: dict[str, int] = {p.id: 0 for p in self.patterns}
            day_signals: dict[str, int] = {p.id: 0 for p in self.patterns}

            for symbol in symbols:
                df = symbol_data[symbol]
                step += 1
                if progress_callback:
                    progress_callback(
                        step,
                        total_steps,
                        f"{eval_date.isoformat()} · {symbol} ({step}/{total_steps})",
                        pattern_results,
                    )
                if step_delay_sec > 0:
                    time.sleep(step_delay_sec)

                idx_list = df.index[df["trade_date"] == eval_date].tolist()
                if not idx_list:
                    continue
                idx = idx_list[0]
                pos = df.index.get_loc(idx)
                if pos < self.lookback_days:
                    continue

                lookback_df = df.iloc[pos - self.lookback_days : pos].copy()
                prev_close = float(df.iloc[pos - 1]["close"])
                curr_close = float(df.iloc[pos]["close"])
                actual = _actual_signal(prev_close, curr_close)
                if actual == Signal.NEUTRAL:
                    continue

                for pattern in self.patterns:
                    if progress_callback:
                        progress_callback(
                            step,
                            total_steps,
                            f"{eval_date.isoformat()} · {symbol} ({step}/{total_steps}) · {pattern.name}",
                            pattern_results,
                        )
                    try:
                        signal = pattern.evaluate(lookback_df)
                    except Exception:
                        continue
                    if signal == Signal.NEUTRAL:
                        continue
                    if count_signal is not None and signal != count_signal:
                        continue

                    pr = pattern_results[pattern.id]
                    correct = signal == actual
                    pr.total_signals += 1
                    pr.stock_signals[symbol] = pr.stock_signals.get(symbol, 0) + 1
                    if correct:
                        pr.total_correct += 1
                        pr.stock_correct[symbol] = pr.stock_correct.get(symbol, 0) + 1
                        day_correct[pattern.id] += 1
                    day_signals[pattern.id] += 1
                    pr.day_details.append(
                        DayResult(
                            trade_date=eval_date,
                            symbol=symbol,
                            signal=signal,
                            actual=actual,
                            correct=correct,
                            prev_close=prev_close,
                            predicted_close=_predicted_close(signal, prev_close, lookback_df),
                            actual_close=curr_close,
                        )
                    )

            for pattern in self.patterns:
                if day_signals[pattern.id] > 0:
                    pattern_results[pattern.id].daily_scores.append(float(day_correct[pattern.id]))

        if progress_callback:
            progress_callback(total_steps, total_steps, "Finalizing rankings…", pattern_results)

        ranked = sorted(
            pattern_results.values(),
            key=lambda r: (r.avg_daily_score, r.overall_hit_rate),
            reverse=True,
        )
        return BacktestReport(
            eval_days=self.eval_days,
            lookback_days=self.lookback_days,
            stock_count=len(symbol_data),
            patterns=ranked,
            universe=self.universe,
            symbols=list(symbol_data.keys()),
        )

    async def run(
        self,
        session: AsyncSession,
        progress_callback: ProgressCallback | None = None,
        step_delay_sec: float = 0.0,
    ) -> BacktestReport:
        log.info("Backtest starting symbols=%d universe=%s", len(self.symbols), self.universe)
        symbol_data: dict[str, pd.DataFrame] = {}
        load_total = len(self.symbols)
        load_share = 12

        def _load_progress(i: int, total: int, message: str, partial: dict[str, PatternResult] | None) -> None:
            if progress_callback:
                ratio = i / max(total, 1)
                progress_callback(int(ratio * load_share), 100, message, partial)

        def _sim_progress(
            current: int,
            total: int,
            message: str,
            partial: dict[str, PatternResult] | None,
        ) -> None:
            if progress_callback:
                ratio = current / max(total, 1)
                synthetic = load_share + int(ratio * (100 - load_share))
                progress_callback(synthetic, 100, message, partial)

        for i, symbol in enumerate(self.symbols, start=1):
            _load_progress(
                i,
                load_total,
                f"Loading candles for {symbol} ({i}/{load_total})…",
                None,
            )
            df = await self.load_symbol_candles(session, symbol)
            if df is not None:
                symbol_data[symbol] = df
            if step_delay_sec > 0:
                await asyncio.sleep(step_delay_sec * 0.5)
            elif i % 25 == 0:
                # Yield so isolated job loop stays responsive to cancellation/progress.
                await asyncio.sleep(0)
        # CPU-heavy pattern scan off the event loop thread.
        report = await asyncio.to_thread(
            self.run_on_data,
            symbol_data,
            _sim_progress,
            step_delay_sec,
        )
        log.info(
            "Backtest finished patterns=%d symbols=%d",
            len(report.patterns),
            len(symbol_data),
        )
        return report

    async def run_latest_prediction(
        self,
        session: AsyncSession,
        progress_callback: ProgressCallback | None = None,
    ) -> LatestPredictionReport | None:
        symbol_data: dict[str, pd.DataFrame] = {}
        load_total = len(self.symbols)
        for i, symbol in enumerate(self.symbols, start=1):
            if progress_callback:
                progress_callback(
                    i,
                    load_total,
                    f"Loading candles for {symbol} ({i}/{load_total})…",
                    None,
                )
            df = await self.load_symbol_candles_for_prediction(session, symbol)
            if df is not None:
                symbol_data[symbol] = df
            if i % 25 == 0:
                await asyncio.sleep(0)
        return await asyncio.to_thread(self.run_latest_prediction_on_data, symbol_data)

    async def persist(
        self,
        session: AsyncSession,
        report: BacktestReport,
        simulation_date: date | None = None,
    ) -> BacktestRun:
        from sqlalchemy import delete

        from app.services.simulation_cache import serialize_report, today_ist

        _, _, BacktestRun, BacktestPatternScore, BacktestStockScore = _orm_models()
        sim_date = simulation_date or today_ist()
        uni = report.universe.upper()

        existing_run_ids = list(
            await session.scalars(
                select(BacktestRun.id).where(
                    BacktestRun.simulation_date == sim_date,
                    BacktestRun.universe == uni,
                )
            )
        )
        if existing_run_ids:
            await session.execute(
                delete(BacktestStockScore).where(BacktestStockScore.run_id.in_(existing_run_ids))
            )
            await session.execute(
                delete(BacktestPatternScore).where(
                    BacktestPatternScore.run_id.in_(existing_run_ids)
                )
            )
            await session.execute(delete(BacktestRun).where(BacktestRun.id.in_(existing_run_ids)))
            await session.flush()

        run = BacktestRun(
            eval_days=report.eval_days,
            lookback_days=report.lookback_days,
            stock_count=report.stock_count,
            universe=uni,
            simulation_date=sim_date,
            report_payload=serialize_report(report),
        )
        session.add(run)
        await session.flush()

        for rank, pr in enumerate(report.patterns, start=1):
            session.add(
                BacktestPatternScore(
                    run_id=run.id,
                    pattern_id=pr.pattern_id,
                    pattern_name=pr.pattern_name,
                    total_correct=pr.total_correct,
                    total_signals=pr.total_signals,
                    avg_daily_score=Decimal(str(round(pr.avg_daily_score, 2))),
                    overall_hit_rate=Decimal(str(round(pr.overall_hit_rate, 2))),
                    rank=rank,
                )
            )
            for symbol in set(pr.stock_signals.keys()) | set(pr.stock_correct.keys()):
                signals = pr.stock_signals.get(symbol, 0)
                correct = pr.stock_correct.get(symbol, 0)
                hit = (correct / signals * 100) if signals else 0
                session.add(
                    BacktestStockScore(
                        run_id=run.id,
                        pattern_id=pr.pattern_id,
                        symbol=symbol,
                        correct=correct,
                        signals=signals,
                        hit_rate=Decimal(str(round(hit, 2))),
                    )
                )

        await session.commit()
        await session.refresh(run)
        return run
