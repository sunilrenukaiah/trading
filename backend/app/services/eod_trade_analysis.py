"""End-of-day analysis for recommendation bracket trades and tomorrow's reasoning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pandas as pd
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import app.strategies.patterns  # noqa: F401
from app.models import OhlcvCandle, PaperTradePlan, RecommendationSnapshot, TradePlanStatus
from app.services.backtest import (
    BacktestEngine,
    _actual_signal,
    _predicted_close,
)
from app.services.market_calendar import (
    active_market_session_date,
    closed_in_square_off_window,
    is_post_session_eod_ready,
    is_trading_day_complete,
    last_completed_trading_day,
)

POST_SESSION_STATUS = (
    "Today's session is still in progress — this analysis runs after 3:45 PM IST "
    "once the trading day is complete."
)
from app.services.paper_trading import PaperTradingService
from app.services.recommendation_engine import (
    PatternRanking,
    _load_universe,
    _tier_display,
    all_report_recommendations,
    load_market_universe_candles_from_db,
    market_universe_symbol_set,
    recommendation_pattern_rankings,
    symbol_tier_map_from_data,
)
from app.services.recommendation_cache import deserialize_snapshot
from app.strategies.base import Signal
from app.strategies.registry import get_all_patterns

LOOKBACK_DAYS = 20
MIN_MISSED_RETURN_PCT = 0.25
MAX_MISSED_PROFITABLE_ROWS = 20


@dataclass
class AlternativePatternRow:
    pattern_id: str
    pattern_name: str
    signal: str
    correct: bool
    predicted_close: float
    hypothetical_exit: float
    hypothetical_pnl: float
    pnl_vs_actual: float


@dataclass
class TradeAnalysisRow:
    symbol: str
    pattern_used: str
    shares: int
    plan_status: str
    entry_made: bool
    entry_price: float | None
    touched_stop: bool
    touched_target: bool
    target_price: float
    stop_loss_price: float
    day_open: float | None
    day_high: float | None
    day_low: float | None
    day_close: float | None
    close_vs_target: str
    close_vs_target_pct: float | None
    exit_price: float | None
    realized_pnl: float | None
    mark_to_market_pnl: float | None
    target_missed: bool
    target_miss_pct: float | None
    better_patterns: list[AlternativePatternRow] = field(default_factory=list)


@dataclass
class ExecutedTradeReviewRow:
    symbol: str
    pattern_used: str
    shares: int
    plan_status: str
    entry_price: float
    day_high: float
    exit_price: float
    pattern_target: float
    peak_pnl_inr: float
    actual_pnl_inr: float
    left_on_table_inr: float
    left_on_table_pct: float
    exit_vs_target_pct: float
    peak_vs_target_pct: float
    peak_reached_target: bool
    sold_below_peak: bool
    summary: str


@dataclass
class MissedProfitableTradeRow:
    symbol: str
    cap_tier: str
    prev_close: float
    day_close: float
    day_return_pct: float
    why_missed: str
    top_pattern_signals: str
    catching_pattern: str | None
    catching_pattern_hit_rate: float | None
    lesson: str


@dataclass
class ReasoningInsight:
    priority: int
    category: str
    title: str
    detail: str


@dataclass
class EodTradeAnalysisReport:
    trade_date: date
    as_of_date: date
    total_plans: int
    entries_made: int
    no_entry: int
    touched_stop_loss: int
    touched_target: int
    closed_above_target: int
    closed_below_target: int
    target_hit_exits: int
    stop_hit_exits: int
    time_exit_exits: int
    open_at_close: int
    missed_target_trades: int
    square_off_missed_targets: int
    square_off_325_exits: int
    eod_close_exits: int
    avg_target_miss_pct: float | None
    day_realized_pnl: float
    trades: list[TradeAnalysisRow]
    executed_trade_reviews: list[ExecutedTradeReviewRow] = field(default_factory=list)
    executed_trade_lessons: list[str] = field(default_factory=list)
    missed_profitable_trades: list[MissedProfitableTradeRow] = field(default_factory=list)
    missed_profitable_lessons: list[str] = field(default_factory=list)
    post_session_ready: bool = True
    post_session_status: str | None = None
    insights: list[ReasoningInsight] = field(default_factory=list)
    tomorrow_actions: list[str] = field(default_factory=list)


def _close_vs_target_label(day_close: float | None, target: float) -> tuple[str, float | None]:
    if day_close is None:
        return "N/A", None
    pct = round((day_close - target) / target * 100, 2)
    if abs(pct) < 0.05:
        return "At target", pct
    if day_close > target:
        return "Above target", pct
    return "Below target", pct


def _plan_entry_made(plan: PaperTradePlan) -> bool:
    if plan.entry_price is not None:
        return True
    return plan.status in (
        TradePlanStatus.OPEN,
        TradePlanStatus.TARGET_HIT,
        TradePlanStatus.STOP_HIT,
        TradePlanStatus.TIME_EXIT,
    )


def _actual_pnl_for_plan(
    plan: PaperTradePlan,
    *,
    entry_price: float,
    day_close: float | None,
) -> float:
    if plan.realized_pnl is not None:
        return float(plan.realized_pnl)
    if day_close is not None and _plan_entry_made(plan):
        return round((day_close - entry_price) * plan.shares, 2)
    return 0.0


def _evaluate_alternative_patterns(
    df: pd.DataFrame,
    trade_date: date,
    *,
    entry_price: float,
    shares: int,
    actual_pnl: float,
    used_pattern: str,
) -> list[AlternativePatternRow]:
    dates = df["trade_date"].tolist()
    if trade_date not in dates:
        return []

    pos = dates.index(trade_date)
    if pos < LOOKBACK_DAYS:
        return []

    lookback_df = df.iloc[pos - LOOKBACK_DAYS : pos].copy()
    prev_close = float(df.iloc[pos - 1]["close"])
    actual_close = float(df.iloc[pos]["close"])
    day_high = float(df.iloc[pos]["high"])
    actual = _actual_signal(prev_close, actual_close)

    alternatives: list[AlternativePatternRow] = []
    for pattern in get_all_patterns():
        if pattern.name == used_pattern:
            continue
        signal = pattern.evaluate(lookback_df)
        if signal != Signal.BULLISH:
            continue

        predicted = _predicted_close(signal, prev_close, lookback_df)
        correct = signal == actual and actual == Signal.BULLISH
        if correct:
            hyp_exit = min(max(predicted, entry_price), day_high)
        else:
            hyp_exit = actual_close
        hyp_pnl = round((hyp_exit - entry_price) * shares, 2)
        delta = round(hyp_pnl - actual_pnl, 2)
        if correct and delta > 0:
            alternatives.append(
                AlternativePatternRow(
                    pattern_id=pattern.id,
                    pattern_name=pattern.name,
                    signal=signal.value,
                    correct=correct,
                    predicted_close=round(predicted, 2),
                    hypothetical_exit=round(hyp_exit, 2),
                    hypothetical_pnl=hyp_pnl,
                    pnl_vs_actual=delta,
                )
            )

    alternatives.sort(key=lambda row: row.pnl_vs_actual, reverse=True)
    return alternatives[:5]


def _format_pattern_signals(signals: dict[str, str]) -> str:
    if not signals:
        return "—"
    return ", ".join(f"{name}: {sig}" for name, sig in signals.items())


def _diagnose_why_missed(
    *,
    top_patterns: list[PatternRanking],
    lookback_df: pd.DataFrame,
    min_hit_rate: float,
) -> tuple[str, dict[str, str]]:
    pattern_map = {p.id: p for p in get_all_patterns()}
    signals: dict[str, str] = {}
    bullish_qualified: list[str] = []
    bearish_names: list[str] = []
    neutral_names: list[str] = []

    for ranking in top_patterns:
        pattern = pattern_map.get(ranking.pattern_id)
        if pattern is None:
            continue
        signal = pattern.evaluate(lookback_df)
        signals[ranking.pattern_name] = signal.value
        if signal == Signal.BULLISH and ranking.hit_rate_pct >= min_hit_rate:
            bullish_qualified.append(ranking.pattern_name)
        elif signal == Signal.BEARISH:
            bearish_names.append(ranking.pattern_name)
        elif signal == Signal.NEUTRAL:
            neutral_names.append(ranking.pattern_name)

    if bullish_qualified:
        reason = (
            f"Top pattern(s) fired bullish ({', '.join(bullish_qualified)}) but the stock "
            f"was not picked — likely outranked by higher-confidence peers or tier/bucket limits."
        )
    elif bearish_names:
        reason = f"Top-ranked patterns were bearish: {', '.join(bearish_names)}."
    elif neutral_names:
        reason = f"No top pattern fired bullish — signals were neutral ({', '.join(neutral_names)})."
    else:
        reason = "No qualified top pattern produced a bullish signal on the prior session."

    return reason, signals


def _best_catching_pattern(
    lookback_df: pd.DataFrame,
    *,
    prev_close: float,
    day_close: float,
    pattern_hit: dict[str, float],
) -> tuple[str | None, float | None]:
    actual = _actual_signal(prev_close, day_close)
    if actual != Signal.BULLISH:
        return None, None

    candidates: list[tuple[str, float]] = []
    for pattern in get_all_patterns():
        if pattern.evaluate(lookback_df) != Signal.BULLISH:
            continue
        rate = pattern_hit.get(pattern.id, 0.0)
        candidates.append((pattern.name, rate))

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: item[1], reverse=True)
    name, rate = candidates[0]
    return name, round(rate, 1) if rate else None


def _lesson_for_missed_row(row: MissedProfitableTradeRow) -> str:
    if row.catching_pattern:
        return (
            f"{row.symbol} (+{row.day_return_pct:.1f}%): {row.why_missed} "
            f"Watch {row.catching_pattern} on similar setups."
        )
    return f"{row.symbol} (+{row.day_return_pct:.1f}%): {row.why_missed}"


def _analyze_missed_profitable_symbol(
    df: pd.DataFrame,
    trade_date: date,
    *,
    symbol: str,
    cap_tier: str,
    top_patterns: list[PatternRanking],
    min_hit_rate: float,
    pattern_hit: dict[str, float],
) -> MissedProfitableTradeRow | None:
    dates = df["trade_date"].tolist()
    normalized = [d.date() if hasattr(d, "date") else d for d in dates]
    if trade_date not in normalized:
        return None

    pos = normalized.index(trade_date)
    if pos < LOOKBACK_DAYS:
        return None

    prev_close = float(df.iloc[pos - 1]["close"])
    day_close = float(df.iloc[pos]["close"])
    if prev_close <= 0:
        return None

    day_return_pct = round((day_close - prev_close) / prev_close * 100, 2)
    if day_return_pct < MIN_MISSED_RETURN_PCT:
        return None
    if _actual_signal(prev_close, day_close) != Signal.BULLISH:
        return None

    lookback_df = df.iloc[pos - LOOKBACK_DAYS : pos].copy()
    why_missed, signals = _diagnose_why_missed(
        top_patterns=top_patterns,
        lookback_df=lookback_df,
        min_hit_rate=min_hit_rate,
    )
    catching_pattern, catching_rate = _best_catching_pattern(
        lookback_df,
        prev_close=prev_close,
        day_close=day_close,
        pattern_hit=pattern_hit,
    )

    row = MissedProfitableTradeRow(
        symbol=symbol,
        cap_tier=cap_tier,
        prev_close=round(prev_close, 2),
        day_close=round(day_close, 2),
        day_return_pct=day_return_pct,
        why_missed=why_missed,
        top_pattern_signals=_format_pattern_signals(signals),
        catching_pattern=catching_pattern,
        catching_pattern_hit_rate=catching_rate,
        lesson="",
    )
    row.lesson = _lesson_for_missed_row(row)
    return row


def _build_executed_trade_review(trade: TradeAnalysisRow) -> ExecutedTradeReviewRow | None:
    if not trade.entry_made or trade.entry_price is None or trade.day_high is None:
        return None

    exit_price = trade.exit_price if trade.exit_price is not None else trade.day_close
    if exit_price is None:
        return None

    entry = trade.entry_price
    peak = trade.day_high
    target = trade.target_price
    shares = trade.shares

    peak_pnl = round((peak - entry) * shares, 2)
    actual_pnl = round((exit_price - entry) * shares, 2)
    left_on_table = round(max(0.0, peak_pnl - actual_pnl), 2)
    left_on_table_pct = round((peak - exit_price) / exit_price * 100, 2) if exit_price > 0 else 0.0
    exit_vs_target_pct = round((exit_price - target) / target * 100, 2)
    peak_vs_target_pct = round((peak - target) / target * 100, 2)
    peak_reached_target = peak >= target
    sold_below_peak = exit_price < peak - 0.005

    if left_on_table > 0:
        summary = (
            f"Peak ₹{peak:,.2f} could have earned ₹{peak_pnl:,.0f} "
            f"(₹{left_on_table:,.0f} more than the ₹{actual_pnl:,.0f} at exit ₹{exit_price:,.2f})."
        )
    elif peak_reached_target and exit_vs_target_pct >= -0.05:
        summary = f"Captured the pattern target — exited at ₹{exit_price:,.2f} near target ₹{target:,.2f}."
    else:
        summary = (
            f"Exited at ₹{exit_price:,.2f} vs pattern target ₹{target:,.2f} "
            f"({exit_vs_target_pct:+.1f}%); intraday peak was ₹{peak:,.2f}."
        )

    return ExecutedTradeReviewRow(
        symbol=trade.symbol,
        pattern_used=trade.pattern_used,
        shares=shares,
        plan_status=trade.plan_status,
        entry_price=round(entry, 2),
        day_high=round(peak, 2),
        exit_price=round(exit_price, 2),
        pattern_target=round(target, 2),
        peak_pnl_inr=peak_pnl,
        actual_pnl_inr=actual_pnl,
        left_on_table_inr=left_on_table,
        left_on_table_pct=left_on_table_pct,
        exit_vs_target_pct=exit_vs_target_pct,
        peak_vs_target_pct=peak_vs_target_pct,
        peak_reached_target=peak_reached_target,
        sold_below_peak=sold_below_peak,
        summary=summary,
    )


def build_executed_trade_reviews(trades: list[TradeAnalysisRow]) -> list[ExecutedTradeReviewRow]:
    rows = [_build_executed_trade_review(trade) for trade in trades]
    reviews = [row for row in rows if row is not None]
    reviews.sort(key=lambda row: row.left_on_table_inr, reverse=True)
    return reviews


def build_executed_trade_lessons(reviews: list[ExecutedTradeReviewRow]) -> list[str]:
    if not reviews:
        return []

    lessons: list[str] = []
    total_left = round(sum(row.left_on_table_inr for row in reviews), 2)
    below_peak = [row for row in reviews if row.sold_below_peak and row.left_on_table_inr > 0]
    peak_hit_target = [row for row in reviews if row.peak_reached_target and row.exit_vs_target_pct < -0.05]

    lessons.append(
        f"{len(reviews)} executed trade(s) reviewed — combined ₹{total_left:,.0f} additional profit "
        f"was available at intraday peaks vs actual exits."
    )

    if below_peak:
        top = max(below_peak, key=lambda row: row.left_on_table_inr)
        lessons.append(
            f"{len(below_peak)} position(s) sold below the day's high; largest gap: {top.symbol} "
            f"(₹{top.left_on_table_inr:,.0f} left on table, peak ₹{top.day_high:,.2f} vs exit ₹{top.exit_price:,.2f})."
        )

    if peak_hit_target:
        symbols = ", ".join(row.symbol for row in peak_hit_target[:3])
        lessons.append(
            f"{len(peak_hit_target)} trade(s) touched the pattern target intraday but exited below it "
            f"({symbols}) — review trailing stops or partial exits near target."
        )

    for row in reviews[:3]:
        if row.left_on_table_inr > 0:
            lessons.append(row.summary)

    return lessons


def build_missed_profitable_lessons(rows: list[MissedProfitableTradeRow]) -> list[str]:
    if not rows:
        return []

    lessons: list[str] = []
    lessons.append(
        f"{len(rows)} universe stock(s) finished up ≥{MIN_MISSED_RETURN_PCT:g}% today "
        f"but were not in our recommendation picks."
    )

    pattern_counts: dict[str, int] = {}
    for row in rows:
        if row.catching_pattern:
            pattern_counts[row.catching_pattern] = pattern_counts.get(row.catching_pattern, 0) + 1

    if pattern_counts:
        ranked = sorted(pattern_counts.items(), key=lambda item: item[1], reverse=True)
        top_name, top_count = ranked[0]
        lessons.append(
            f"{top_name} would have flagged {top_count} of these movers — consider boosting "
            f"its weight or widening tier picks when it aligns with other bullish patterns."
        )
        if len(ranked) > 1:
            others = ", ".join(name for name, _ in ranked[1:4])
            lessons.append(f"Other useful patterns today: {others}.")

    bearish_misses = sum(1 for row in rows if "bearish" in row.why_missed.lower())
    if bearish_misses >= 2:
        lessons.append(
            f"{bearish_misses} misses had bearish top-pattern signals while price rallied — "
            "review whether recent pattern lookbacks are too short for reversal setups."
        )

    neutral_misses = sum(1 for row in rows if "neutral" in row.why_missed.lower())
    if neutral_misses >= 2:
        lessons.append(
            f"{neutral_misses} misses had neutral top-pattern signals — expand pattern scan "
            "or lower the minimum hit-rate floor when volume confirms a breakout."
        )

    ranked_movers = sorted(rows, key=lambda row: row.day_return_pct, reverse=True)[:3]
    for row in ranked_movers:
        lessons.append(row.lesson)

    return lessons


def build_reasoning(report: EodTradeAnalysisReport) -> tuple[list[ReasoningInsight], list[str]]:
    insights: list[ReasoningInsight] = []
    actions: list[str] = []

    if report.total_plans == 0:
        insights.append(
            ReasoningInsight(
                priority=1,
                category="setup",
                title="No trades to analyze",
                detail="Place bracket orders from the Recommendations tab to generate an EOD report.",
            )
        )
        actions.append("Run recommendation analysis and place orders before the next session.")
        return insights, actions

    entry_rate = report.entries_made / report.total_plans
    if report.no_entry > 0:
        insights.append(
            ReasoningInsight(
                priority=1,
                category="entry",
                title="Limit entries did not fill",
                detail=(
                    f"{report.no_entry} of {report.total_plans} orders never entered "
                    f"({entry_rate:.0%} fill rate). Price likely stayed above your buy limits."
                ),
            )
        )
        actions.append(
            "For tomorrow: set buy limits closer to the previous close or add a small buffer below resistance."
        )

    if report.touched_stop_loss > report.touched_target:
        insights.append(
            ReasoningInsight(
                priority=2,
                category="risk",
                title="Stop loss touched more than target",
                detail=(
                    f"{report.touched_stop_loss} stocks touched stop loss vs "
                    f"{report.touched_target} that touched target intraday."
                ),
            )
        )
        actions.append(
            "Review pattern hit rates on the Pattern backtest tab — drop patterns with weak recent accuracy."
        )

    if report.closed_below_target > report.closed_above_target and report.entries_made:
        insights.append(
            ReasoningInsight(
                priority=2,
                category="exit",
                title="Most positions closed below target",
                detail=(
                    f"{report.closed_below_target} stocks finished below target at EOD vs "
                    f"{report.closed_above_target} above target."
                ),
            )
        )
        actions.append(
            "Consider partial profit booking when price crosses 70–80% of target, or use trailing stops."
        )

    if report.missed_target_trades > 0 and report.avg_target_miss_pct is not None:
        square_off_note = ""
        if report.square_off_missed_targets > 0:
            if report.square_off_325_exits > 0:
                square_off_note = (
                    f" ({report.square_off_missed_targets} closed at 3:25 PM square-off "
                    f"without reaching target)"
                )
            else:
                square_off_note = (
                    f" ({report.square_off_missed_targets} squared off before session end "
                    f"without reaching target)"
                )
        insights.append(
            ReasoningInsight(
                priority=2,
                category="target",
                title="Executed trades missed target",
                detail=(
                    f"{report.missed_target_trades} entered positions did not reach target — "
                    f"average miss {report.avg_target_miss_pct:.1f}% below target."
                    f"{square_off_note}"
                ),
            )
        )
        actions.append(
            "Recalibrate sell targets using recent ATR/volatility rather than fixed model percentages."
        )

    if report.time_exit_exits > 0:
        if report.square_off_325_exits > 0:
            insights.append(
                ReasoningInsight(
                    priority=2,
                    category="square_off",
                    title="3:25 PM square-off exits",
                    detail=(
                        f"{report.square_off_325_exits} position(s) were auto-sold at 3:25 PM "
                        f"because target/stop had not triggered earlier."
                    ),
                )
            )
        if report.eod_close_exits > 0:
            if is_trading_day_complete(report.trade_date):
                eod_detail = (
                    f"{report.eod_close_exits} position(s) were squared off at the day's "
                    f"closing price because target/stop had not triggered."
                )
            else:
                eod_detail = (
                    f"{report.eod_close_exits} position(s) show a square-off exit before "
                    f"3:25 PM — the session is still open, so this is not a scheduled "
                    f"3:25 PM exit (likely from an intraday sync using prior-day EOD rules)."
                )
            insights.append(
                ReasoningInsight(
                    priority=2,
                    category="square_off",
                    title="EOD square-off exits",
                    detail=eod_detail,
                )
            )
        if report.square_off_missed_targets > 0 and report.square_off_325_exits > 0:
            actions.append(
                "Review stocks sold at square-off — consider tighter targets or earlier partial exits."
            )

    alt_pattern_counts: dict[str, int] = {}
    alt_pattern_delta: dict[str, float] = {}
    for trade in report.trades:
        for alt in trade.better_patterns:
            alt_pattern_counts[alt.pattern_name] = alt_pattern_counts.get(alt.pattern_name, 0) + 1
            alt_pattern_delta[alt.pattern_name] = (
                alt_pattern_delta.get(alt.pattern_name, 0.0) + alt.pnl_vs_actual
            )

    if alt_pattern_counts:
        ranked = sorted(
            alt_pattern_counts.items(),
            key=lambda item: (item[1], alt_pattern_delta.get(item[0], 0.0)),
            reverse=True,
        )
        top_name, top_count = ranked[0]
        extra_pnl = alt_pattern_delta.get(top_name, 0.0)
        insights.append(
            ReasoningInsight(
                priority=3,
                category="pattern",
                title="Alternative patterns would have paid more",
                detail=(
                    f"{top_name} was correct and more profitable on {top_count} stock(s) today "
                    f"(combined ₹{extra_pnl:,.0f} extra vs actual exits)."
                ),
            )
        )
        names = ", ".join(name for name, _ in ranked[:3])
        actions.append(
            f"Boost ranking weight for {names} in tomorrow's recommendation scan."
        )

    if report.post_session_ready and report.missed_profitable_trades:
        top_mover = max(report.missed_profitable_trades, key=lambda row: row.day_return_pct)
        catching = top_mover.catching_pattern or "a lower-ranked pattern"
        insights.append(
            ReasoningInsight(
                priority=3,
                category="missed",
                title="NIFTY250 profitable closes not recommended",
                detail=(
                    f"{len(report.missed_profitable_trades)} universe stock(s) rallied "
                    f"≥{MIN_MISSED_RETURN_PCT:g}% without a recommendation — "
                    f"biggest mover {top_mover.symbol} (+{top_mover.day_return_pct:.1f}%). "
                    f"{catching} could have flagged similar setups."
                ),
            )
        )
        if report.missed_profitable_lessons:
            actions.append(report.missed_profitable_lessons[1] if len(report.missed_profitable_lessons) > 1 else report.missed_profitable_lessons[0])

    if report.post_session_ready and report.executed_trade_reviews:
        total_left = round(sum(row.left_on_table_inr for row in report.executed_trade_reviews), 2)
        if total_left > 0:
            top = max(report.executed_trade_reviews, key=lambda row: row.left_on_table_inr)
            insights.append(
                ReasoningInsight(
                    priority=3,
                    category="exit",
                    title="Additional profit at intraday peaks",
                    detail=(
                        f"Executed trades left ₹{total_left:,.0f} on the table vs intraday highs — "
                        f"biggest gap on {top.symbol} (peak ₹{top.day_high:,.2f}, sold ₹{top.exit_price:,.2f}, "
                        f"pattern target ₹{top.pattern_target:,.2f})."
                    ),
                )
            )
            if report.executed_trade_lessons and len(report.executed_trade_lessons) > 1:
                actions.append(report.executed_trade_lessons[1])

    if report.target_hit_exits > 0 and report.day_realized_pnl > 0:
        insights.append(
            ReasoningInsight(
                priority=4,
                category="performance",
                title="Winners captured",
                detail=(
                    f"{report.target_hit_exits} positions hit target with "
                    f"₹{report.day_realized_pnl:,.0f} day realized P&L."
                ),
            )
        )

    if not actions:
        actions.append(
            "Maintain current pattern filters and bracket levels — no major gaps detected today."
        )

    insights.sort(key=lambda item: item.priority)
    return insights, actions


class EodTradeAnalysisService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.paper = PaperTradingService(session)
        self.engine = BacktestEngine(lookback_days=LOOKBACK_DAYS, eval_days=1)

    async def list_trade_dates(self) -> list[date]:
        account = await self.paper.get_default_account()
        rows = (
            await self.session.scalars(
                select(distinct(PaperTradePlan.recommendation_date))
                .where(PaperTradePlan.account_id == account.id)
                .order_by(PaperTradePlan.recommendation_date.desc())
            )
        ).all()
        return list(rows)

    async def _candle_for_date(self, instrument_id: int, trade_date: date) -> OhlcvCandle | None:
        return await self.session.scalar(
            select(OhlcvCandle).where(
                OhlcvCandle.instrument_id == instrument_id,
                OhlcvCandle.trade_date == trade_date,
            )
        )

    async def _day_ohlc_for_plan(
        self,
        plan: PaperTradePlan,
        trade_date: date,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """Return (open, high, low, close) from DB candle or live session quote for today."""
        import asyncio

        candle = await self._candle_for_date(plan.instrument_id, trade_date)
        if candle is not None:
            return (
                float(candle.open),
                float(candle.high),
                float(candle.low),
                float(candle.close),
            )

        if trade_date != active_market_session_date():
            return None, None, None, None

        from app.services.intraday_chart import fetch_session_ohlc_sync

        quote = await asyncio.to_thread(fetch_session_ohlc_sync, plan.instrument.symbol)
        return (
            quote.get("open"),
            quote.get("high"),
            quote.get("low"),
            quote.get("last"),
        )

    async def _recommendation_context(
        self,
        trade_date: date,
    ) -> tuple[set[str], list[PatternRanking], float, dict[str, float], dict[str, pd.DataFrame]]:
        """Symbols recommended for trade_date plus top-pattern rankings from snapshot or live calc."""
        cfg = _load_universe()
        min_hit_rate = float(cfg.get("min_pattern_hit_rate_pct", 55))
        top_n = int(cfg.get("top_patterns_count", 3))
        lookback = int(cfg.get("lookback_days", LOOKBACK_DAYS))
        eval_days = int(cfg.get("eval_days", 15))

        recommended: set[str] = set()
        top_patterns: list[PatternRanking] = []

        row = await self.session.scalar(
            select(RecommendationSnapshot)
            .where(RecommendationSnapshot.prediction_date == trade_date)
            .order_by(RecommendationSnapshot.generated_at.desc())
            .limit(1)
        )
        if row is not None:
            payload = row.payload
            if isinstance(payload, str):
                import json

                payload = json.loads(payload)
            report, _, _, _ = deserialize_snapshot(payload)
            recommended = {rec.symbol.upper() for rec in all_report_recommendations(report)}
            top_patterns = list(report.top_patterns)

        symbol_data = await load_market_universe_candles_from_db(
            self.session, min_rows=25, allowed=market_universe_symbol_set()
        )

        if symbol_data:
            all_rankings, computed_top = recommendation_pattern_rankings(
                symbol_data,
                eval_days=eval_days,
                lookback_days=lookback,
                min_hit_rate=min_hit_rate,
                top_n=top_n,
            )
            if not top_patterns:
                top_patterns = computed_top
            pattern_hit = {p.pattern_id: p.hit_rate_pct for p in all_rankings}
        else:
            pattern_hit = {}

        return recommended, top_patterns, min_hit_rate, pattern_hit, symbol_data

    async def _build_missed_profitable_trades(
        self,
        trade_date: date,
        *,
        excluded_symbols: set[str],
    ) -> tuple[list[MissedProfitableTradeRow], list[str]]:
        recommended, top_patterns, min_hit_rate, pattern_hit, symbol_data = (
            await self._recommendation_context(trade_date)
        )
        skip = excluded_symbols | recommended
        tier_map = symbol_tier_map_from_data(symbol_data)

        rows: list[MissedProfitableTradeRow] = []
        for symbol, tier_key in tier_map.items():
            if symbol in skip:
                continue
            df = symbol_data.get(symbol)
            if df is None:
                continue
            row = _analyze_missed_profitable_symbol(
                df,
                trade_date,
                symbol=symbol,
                cap_tier=_tier_display(tier_key),
                top_patterns=top_patterns,
                min_hit_rate=min_hit_rate,
                pattern_hit=pattern_hit,
            )
            if row is not None:
                rows.append(row)

        rows.sort(key=lambda item: item.day_return_pct, reverse=True)
        rows = rows[:MAX_MISSED_PROFITABLE_ROWS]
        return rows, build_missed_profitable_lessons(rows)

    async def build_report(
        self,
        trade_date: date | None = None,
        *,
        as_of_date: date | None = None,
    ) -> EodTradeAnalysisReport:
        if trade_date is None:
            trade_date = as_of_date or last_completed_trading_day()
        eval_date = as_of_date or trade_date
        account = await self.paper.get_default_account()

        plans = (
            await self.session.scalars(
                select(PaperTradePlan)
                .where(
                    PaperTradePlan.account_id == account.id,
                    PaperTradePlan.recommendation_date == trade_date,
                )
                .options(selectinload(PaperTradePlan.instrument))
                .order_by(PaperTradePlan.id)
            )
        ).all()

        trades: list[TradeAnalysisRow] = []
        entries = no_entry = touched_stop = touched_target = 0
        closed_above = closed_below = 0
        target_exits = stop_exits = time_exits = open_at_close = 0
        square_off_325 = eod_close = 0
        missed_target = square_off_missed = 0
        miss_pcts: list[float] = []

        for plan in plans:
            day_open, day_high, day_low, day_close = await self._day_ohlc_for_plan(
                plan, trade_date
            )
            target = float(plan.target_price)
            stop = float(plan.stop_loss_price)
            entry_price = float(plan.entry_price) if plan.entry_price else float(plan.entry_limit_price)

            entry_made = _plan_entry_made(plan)
            if entry_made:
                entries += 1
            else:
                no_entry += 1

            bar_touched_stop = day_low is not None and day_low <= stop
            bar_touched_target = day_high is not None and day_high >= target
            if bar_touched_stop:
                touched_stop += 1
            if bar_touched_target:
                touched_target += 1

            close_label, close_pct = _close_vs_target_label(day_close, target)
            if entry_made and day_close is not None:
                if close_label == "Above target":
                    closed_above += 1
                elif close_label == "Below target":
                    closed_below += 1

            if plan.status == TradePlanStatus.TARGET_HIT:
                target_exits += 1
            elif plan.status == TradePlanStatus.STOP_HIT:
                stop_exits += 1
            elif plan.status == TradePlanStatus.TIME_EXIT:
                time_exits += 1
                if plan.closed_at and closed_in_square_off_window(plan.closed_at, trade_date):
                    square_off_325 += 1
                else:
                    eod_close += 1
            elif plan.status == TradePlanStatus.OPEN:
                open_at_close += 1

            exit_price = float(plan.exit_price) if plan.exit_price else None
            realized = float(plan.realized_pnl) if plan.realized_pnl is not None else None
            mtm = None
            if entry_made and day_close is not None:
                mtm = round((day_close - entry_price) * plan.shares, 2)

            actual_pnl = _actual_pnl_for_plan(
                plan, entry_price=entry_price, day_close=day_close
            )

            trade_missed = False
            miss_pct = None
            if entry_made and plan.status != TradePlanStatus.TARGET_HIT:
                ref = exit_price if exit_price is not None else day_close
                if ref is not None and ref < target:
                    trade_missed = True
                    miss_pct = round((target - ref) / target * 100, 2)
                    missed_target += 1
                    miss_pcts.append(miss_pct)
                    if plan.status == TradePlanStatus.TIME_EXIT:
                        square_off_missed += 1

            better_patterns: list[AlternativePatternRow] = []
            if entry_made:
                df = await self.engine.load_symbol_candles_for_prediction(
                    self.session, plan.instrument.symbol
                )
                if df is not None:
                    better_patterns = _evaluate_alternative_patterns(
                        df,
                        trade_date,
                        entry_price=entry_price,
                        shares=plan.shares,
                        actual_pnl=actual_pnl,
                        used_pattern=plan.pattern_name or "",
                    )

            trades.append(
                TradeAnalysisRow(
                    symbol=plan.instrument.symbol,
                    pattern_used=plan.pattern_name or "—",
                    shares=plan.shares,
                    plan_status=plan.status.value.replace("_", " ").title(),
                    entry_made=entry_made,
                    entry_price=float(plan.entry_price) if plan.entry_price else None,
                    touched_stop=bar_touched_stop,
                    touched_target=bar_touched_target,
                    target_price=target,
                    stop_loss_price=stop,
                    day_open=day_open,
                    day_high=day_high,
                    day_low=day_low,
                    day_close=day_close,
                    close_vs_target=close_label,
                    close_vs_target_pct=close_pct,
                    exit_price=exit_price,
                    realized_pnl=realized,
                    mark_to_market_pnl=mtm,
                    target_missed=trade_missed,
                    target_miss_pct=miss_pct,
                    better_patterns=better_patterns,
                )
            )

        avg_miss = round(sum(miss_pcts) / len(miss_pcts), 2) if miss_pcts else None

        traded_symbols = {plan.instrument.symbol.upper() for plan in plans}
        post_session_ready = is_post_session_eod_ready(trade_date)
        if post_session_ready:
            executed_reviews = build_executed_trade_reviews(trades)
            executed_lessons = build_executed_trade_lessons(executed_reviews)
            missed_profitable, missed_lessons = await self._build_missed_profitable_trades(
                trade_date,
                excluded_symbols=traded_symbols,
            )
            post_session_status = None
        else:
            executed_reviews, executed_lessons = [], []
            missed_profitable, missed_lessons = [], []
            post_session_status = POST_SESSION_STATUS

        day_pnl = await self.paper.day_realized_pnl_from_trades(trade_date)

        report = EodTradeAnalysisReport(
            trade_date=trade_date,
            as_of_date=eval_date,
            total_plans=len(plans),
            entries_made=entries,
            no_entry=no_entry,
            touched_stop_loss=touched_stop,
            touched_target=touched_target,
            closed_above_target=closed_above,
            closed_below_target=closed_below,
            target_hit_exits=target_exits,
            stop_hit_exits=stop_exits,
            time_exit_exits=time_exits,
            open_at_close=open_at_close,
            missed_target_trades=missed_target,
            square_off_missed_targets=square_off_missed,
            square_off_325_exits=square_off_325,
            eod_close_exits=eod_close,
            avg_target_miss_pct=avg_miss,
            day_realized_pnl=float(day_pnl),
            trades=trades,
            executed_trade_reviews=executed_reviews,
            executed_trade_lessons=executed_lessons,
            missed_profitable_trades=missed_profitable,
            missed_profitable_lessons=missed_lessons,
            post_session_ready=post_session_ready,
            post_session_status=post_session_status,
        )
        insights, actions = build_reasoning(report)
        report.insights = insights
        report.tomorrow_actions = actions
        return report
