"""EOD trade analysis and reasoning engine tests."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.services.eod_trade_analysis import (
    EodTradeAnalysisReport,
    ExecutedTradeReviewRow,
    MissedProfitableTradeRow,
    TradeAnalysisRow,
    _analyze_missed_profitable_symbol,
    _build_executed_trade_review,
    _close_vs_target_label,
    _evaluate_alternative_patterns,
    build_executed_trade_lessons,
    build_executed_trade_reviews,
    build_missed_profitable_lessons,
    build_reasoning,
)
from app.services.recommendation_engine import PatternRanking
from app.strategies.base import Signal


@pytest.mark.quick
def test_close_vs_target_label() -> None:
    label, pct = _close_vs_target_label(105.0, 100.0)
    assert label == "Above target"
    assert pct == 5.0

    label, pct = _close_vs_target_label(95.0, 100.0)
    assert label == "Below target"
    assert pct == -5.0


@pytest.mark.quick
def test_build_reasoning_no_trades() -> None:
    report = EodTradeAnalysisReport(
        trade_date=date(2026, 7, 30),
        as_of_date=date(2026, 7, 30),
        total_plans=0,
        entries_made=0,
        no_entry=0,
        touched_stop_loss=0,
        touched_target=0,
        closed_above_target=0,
        closed_below_target=0,
        target_hit_exits=0,
        stop_hit_exits=0,
        time_exit_exits=0,
        open_at_close=0,
        missed_target_trades=0,
        square_off_missed_targets=0,
        square_off_325_exits=0,
        eod_close_exits=0,
        avg_target_miss_pct=None,
        day_realized_pnl=0.0,
        trades=[],
    )
    insights, actions = build_reasoning(report)
    assert insights
    assert actions


@pytest.mark.quick
def test_build_reasoning_distinguishes_premature_square_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import eod_trade_analysis as mod

    monkeypatch.setattr(mod, "is_trading_day_complete", lambda _d: False)

    report = EodTradeAnalysisReport(
        trade_date=date(2026, 7, 30),
        as_of_date=date(2026, 7, 29),
        total_plans=3,
        entries_made=3,
        no_entry=0,
        touched_stop_loss=0,
        touched_target=0,
        closed_above_target=0,
        closed_below_target=0,
        target_hit_exits=0,
        stop_hit_exits=0,
        time_exit_exits=3,
        open_at_close=0,
        missed_target_trades=3,
        square_off_missed_targets=3,
        square_off_325_exits=0,
        eod_close_exits=3,
        avg_target_miss_pct=1.2,
        day_realized_pnl=0.0,
        trades=[],
    )
    insights, _ = build_reasoning(report)
    titles = [i.title for i in insights]
    assert "3:25 PM square-off exits" not in titles
    assert "EOD square-off exits" in titles
    assert "session is still open" in next(i.detail for i in insights if i.title == "EOD square-off exits")


@pytest.mark.quick
def test_build_missed_profitable_lessons() -> None:
    rows = [
        MissedProfitableTradeRow(
            symbol="CDSL",
            cap_tier="Small Cap",
            prev_close=100.0,
            day_close=105.0,
            day_return_pct=5.0,
            why_missed="Top-ranked patterns were bearish: Doji.",
            top_pattern_signals="Doji: BEARISH",
            catching_pattern="Hammer",
            catching_pattern_hit_rate=62.0,
            lesson="",
        ),
        MissedProfitableTradeRow(
            symbol="IRCTC",
            cap_tier="Small Cap",
            prev_close=200.0,
            day_close=204.0,
            day_return_pct=2.0,
            why_missed="No top pattern fired bullish.",
            top_pattern_signals="Doji: NEUTRAL",
            catching_pattern="Hammer",
            catching_pattern_hit_rate=62.0,
            lesson="",
        ),
    ]
    lessons = build_missed_profitable_lessons(rows)
    assert len(lessons) >= 3
    assert any("Hammer would have flagged" in lesson for lesson in lessons)


@pytest.mark.quick
def test_build_reasoning_skips_missed_profitable_before_cutoff() -> None:
    report = EodTradeAnalysisReport(
        trade_date=date(2026, 7, 30),
        as_of_date=date(2026, 7, 30),
        total_plans=1,
        entries_made=1,
        no_entry=0,
        touched_stop_loss=0,
        touched_target=0,
        closed_above_target=0,
        closed_below_target=0,
        target_hit_exits=0,
        stop_hit_exits=0,
        time_exit_exits=0,
        open_at_close=0,
        missed_target_trades=0,
        square_off_missed_targets=0,
        square_off_325_exits=0,
        eod_close_exits=0,
        avg_target_miss_pct=None,
        day_realized_pnl=0.0,
        trades=[],
        missed_profitable_trades=[
            MissedProfitableTradeRow(
                symbol="CDSL",
                cap_tier="Small Cap",
                prev_close=100.0,
                day_close=105.0,
                day_return_pct=5.0,
                why_missed="bearish",
                top_pattern_signals="Doji: BEARISH",
                catching_pattern="Hammer",
                catching_pattern_hit_rate=62.0,
                lesson="test",
            )
        ],
        post_session_ready=False,
    )
    insights, _ = build_reasoning(report)
    assert not any(i.title == "NIFTY250 profitable closes not recommended" for i in insights)


@pytest.mark.quick
def test_build_executed_trade_review() -> None:
    trade = TradeAnalysisRow(
        symbol="INFY",
        pattern_used="Hammer",
        shares=10,
        plan_status="Time Exit",
        entry_made=True,
        entry_price=1500.0,
        touched_stop=False,
        touched_target=True,
        target_price=1575.0,
        stop_loss_price=1450.0,
        day_open=1505.0,
        day_high=1590.0,
        day_low=1495.0,
        day_close=1550.0,
        close_vs_target="Below target",
        close_vs_target_pct=-1.59,
        exit_price=1550.0,
        realized_pnl=500.0,
        mark_to_market_pnl=None,
        target_missed=True,
        target_miss_pct=1.59,
    )
    review = _build_executed_trade_review(trade)
    assert review is not None
    assert review.left_on_table_inr == 400.0
    assert review.peak_reached_target is True
    assert review.sold_below_peak is True

    reviews = build_executed_trade_reviews([trade])
    lessons = build_executed_trade_lessons(reviews)
    assert len(reviews) == 1
    assert lessons
    assert "left on table" in lessons[1].lower() or "₹400" in lessons[1]


@pytest.mark.quick
def test_build_reasoning_includes_peak_profit_insight() -> None:
    review = ExecutedTradeReviewRow(
        symbol="INFY",
        pattern_used="Hammer",
        shares=10,
        plan_status="Time Exit",
        entry_price=1500.0,
        day_high=1590.0,
        exit_price=1550.0,
        pattern_target=1575.0,
        peak_pnl_inr=900.0,
        actual_pnl_inr=500.0,
        left_on_table_inr=400.0,
        left_on_table_pct=2.58,
        exit_vs_target_pct=-1.59,
        peak_vs_target_pct=0.95,
        peak_reached_target=True,
        sold_below_peak=True,
        summary="test",
    )
    report = EodTradeAnalysisReport(
        trade_date=date(2026, 7, 30),
        as_of_date=date(2026, 7, 30),
        total_plans=1,
        entries_made=1,
        no_entry=0,
        touched_stop_loss=0,
        touched_target=0,
        closed_above_target=0,
        closed_below_target=0,
        target_hit_exits=0,
        stop_hit_exits=0,
        time_exit_exits=1,
        open_at_close=0,
        missed_target_trades=1,
        square_off_missed_targets=0,
        square_off_325_exits=0,
        eod_close_exits=1,
        avg_target_miss_pct=1.59,
        day_realized_pnl=500.0,
        trades=[],
        executed_trade_reviews=[review],
        post_session_ready=True,
    )
    insights, _ = build_reasoning(report)
    assert any(i.title == "Additional profit at intraday peaks" for i in insights)


@pytest.mark.quick
def test_analyze_missed_profitable_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import eod_trade_analysis as mod

    class FakePattern:
        def __init__(self, pid: str, name: str, signal: Signal):
            self.id = pid
            self.name = name
            self._signal = signal

        def evaluate(self, _candles: pd.DataFrame) -> Signal:
            return self._signal

    monkeypatch.setattr(
        mod,
        "get_all_patterns",
        lambda: [
            FakePattern("hammer", "Hammer", Signal.BULLISH),
            FakePattern("doji", "Doji", Signal.BEARISH),
        ],
    )

    dates = [date(2026, 7, d) for d in range(1, 32)]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [100.0] * len(dates),
            "high": [110.0] * len(dates),
            "low": [99.0] * len(dates),
            "close": [100.0] * len(dates),
            "volume": [1000] * len(dates),
        }
    )
    df.iloc[-2, df.columns.get_loc("close")] = 100.0
    df.iloc[-1, df.columns.get_loc("close")] = 106.0

    top_patterns = [
        PatternRanking(
            pattern_id="doji",
            pattern_name="Doji",
            hit_rate_pct=60.0,
            total_correct=6,
            total_signals=10,
            avg_daily_score=0.6,
        )
    ]

    row = _analyze_missed_profitable_symbol(
        df,
        date(2026, 7, 31),
        symbol="CDSL",
        cap_tier="Small Cap",
        top_patterns=top_patterns,
        min_hit_rate=55.0,
        pattern_hit={"hammer": 62.0, "doji": 60.0},
    )
    assert row is not None
    assert row.symbol == "CDSL"
    assert row.catching_pattern == "Hammer"
    assert "bearish" in row.why_missed.lower()


@pytest.mark.quick
def test_build_reasoning_missed_entries() -> None:
    report = EodTradeAnalysisReport(
        trade_date=date(2026, 7, 30),
        as_of_date=date(2026, 7, 30),
        total_plans=4,
        entries_made=1,
        no_entry=3,
        touched_stop_loss=0,
        touched_target=1,
        closed_above_target=0,
        closed_below_target=1,
        target_hit_exits=0,
        stop_hit_exits=0,
        time_exit_exits=0,
        open_at_close=1,
        missed_target_trades=1,
        square_off_missed_targets=0,
        square_off_325_exits=0,
        eod_close_exits=0,
        avg_target_miss_pct=2.5,
        day_realized_pnl=0.0,
        trades=[
            TradeAnalysisRow(
                symbol="INFY",
                pattern_used="Hammer",
                shares=10,
                plan_status="Open",
                entry_made=True,
                entry_price=1500.0,
                touched_stop=False,
                touched_target=True,
                target_price=1575.0,
                stop_loss_price=1450.0,
                day_open=1505.0,
                day_high=1570.0,
                day_low=1495.0,
                day_close=1560.0,
                close_vs_target="Below target",
                close_vs_target_pct=-0.95,
                exit_price=None,
                realized_pnl=None,
                mark_to_market_pnl=600.0,
                target_missed=True,
                target_miss_pct=0.95,
            )
        ],
    )
    insights, actions = build_reasoning(report)
    categories = {i.category for i in insights}
    assert "entry" in categories
    assert "target" in categories
    assert actions


@pytest.mark.quick
def test_evaluate_alternative_patterns_finds_bullish(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import eod_trade_analysis as mod

    class FakePattern:
        def __init__(self, pid: str, name: str, signal: Signal):
            self.id = pid
            self.name = name
            self._signal = signal

        def evaluate(self, candles: pd.DataFrame) -> Signal:
            return self._signal

    monkeypatch.setattr(
        mod,
        "get_all_patterns",
        lambda: [
            FakePattern("hammer", "Hammer", Signal.BULLISH),
            FakePattern("doji", "Doji", Signal.BULLISH),
        ],
    )

    dates = [date(2026, 7, d) for d in range(1, 32)]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [100.0] * len(dates),
            "high": [110.0] * len(dates),
            "low": [99.0] * len(dates),
            "close": [105.0] * len(dates),
            "volume": [1000] * len(dates),
        }
    )
    df.iloc[-2, df.columns.get_loc("close")] = 100.0
    df.iloc[-1, df.columns.get_loc("close")] = 108.0
    df.iloc[-1, df.columns.get_loc("high")] = 112.0

    alts = _evaluate_alternative_patterns(
        df,
        date(2026, 7, 31),
        entry_price=100.0,
        shares=10,
        actual_pnl=-50.0,
        used_pattern="Hammer",
    )
    assert len(alts) == 1
    assert alts[0].pattern_name == "Doji"
    assert alts[0].correct is True


@pytest.mark.quick
@pytest.mark.asyncio
async def test_build_report_as_of_matches_selected_trade_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During today's session, as_of must follow the selected batch date, not last completed day."""
    from datetime import datetime
    from unittest.mock import AsyncMock, MagicMock
    from zoneinfo import ZoneInfo

    from app.services.eod_trade_analysis import EodTradeAnalysisService

    today = date(2026, 8, 3)
    mid_session = datetime(2026, 8, 3, 16, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    monkeypatch.setattr(
        "app.services.eod_trade_analysis.last_completed_trading_day",
        lambda **_: date(2026, 7, 31),
    )
    monkeypatch.setattr(
        "app.services.eod_trade_analysis.active_market_session_date",
        lambda **_: today,
    )
    monkeypatch.setattr(
        "app.services.eod_trade_analysis.is_post_session_eod_ready",
        lambda *_a, **_k: False,
    )

    service = EodTradeAnalysisService(AsyncMock())
    service.paper = AsyncMock()
    service.paper.get_default_account = AsyncMock(return_value=MagicMock(id=1))
    service.paper.day_realized_pnl_from_trades = AsyncMock(return_value=0)
    service.session.scalars = AsyncMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )

    report = await service.build_report(today)

    assert report.trade_date == today
    assert report.as_of_date == today
