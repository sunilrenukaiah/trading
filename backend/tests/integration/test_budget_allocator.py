"""Budget allocation across cap tiers."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.budget_allocator import allocate_budget, BudgetAllocationReport
from app.services.recommendation_engine import (
    PatternRanking,
    RecommendationReport,
    StockRecommendation,
)


def _rec(
    symbol: str,
    tier: str,
    *,
    buy: float = 100.0,
    confidence: float = 70.0,
    hit_rate: float = 60.0,
    price_bucket: str | None = None,
) -> StockRecommendation:
    return StockRecommendation(
        symbol=symbol,
        cap_tier=tier,
        pattern_id="p1",
        pattern_name="Test",
        pattern_hit_rate_30d=hit_rate,
        signal="BULLISH",
        action="BUY",
        buy_price=buy,
        stop_loss=buy * 0.95,
        resistance=buy * 1.05,
        sell_price=buy * 1.1,
        actual_sell_price=buy * 1.05,
        model_profit_pct=10.0,
        actual_profit_pct=5.0,
        risk_reward=1.0,
        latest_close=buy,
        prev_close=buy * 0.99,
        expected_move_pct=5.0,
        confidence_score=confidence,
        price_bucket=price_bucket,
    )


def _report(recs: list[StockRecommendation]) -> RecommendationReport:
    return RecommendationReport(
        generated_at=date.today(),
        prediction_date=date.today(),
        data_through_date=date.today(),
        lookback_days=20,
        eval_days=30,
        top_patterns=[
            PatternRanking("p1", "Test", 60.0, 6, 10, 0.6),
        ],
        recommendations=recs,
        tier_counts={},
    )


@pytest.mark.quick
def test_allocate_budget_splits_by_tier() -> None:
    report = _report(
        [
            _rec("AAA", "Large Cap", buy=100.0, confidence=80.0),
            _rec("BBB", "Mid Cap", buy=200.0, confidence=70.0),
            _rec("CCC", "Small Cap", buy=50.0, confidence=60.0),
        ]
    )
    alloc = allocate_budget(report, 30_000.0, tier_budget_split_pct=33.33)

    assert len(alloc.lines) == 3
    tiers = {line.cap_tier for line in alloc.lines}
    assert tiers == {"Large Cap", "Mid Cap", "Small Cap"}
    # Each tier gets ~₹10k slice — not 100% in one name.
    assert all(line.investment <= 10_500 for line in alloc.lines)
    assert alloc.total_invested < 30_000.0
    assert alloc.cash_remaining > 0


@pytest.mark.quick
def test_allocate_budget_single_tier_does_not_use_full_budget() -> None:
    report = _report([_rec("INFY", "Large Cap", buy=1800.0, confidence=65.0)])
    alloc = allocate_budget(report, 50_000.0, tier_budget_split_pct=33.33)

    assert len(alloc.lines) == 1
    assert alloc.lines[0].investment <= 17_000  # ~⅓ of 50k
    assert alloc.cash_remaining >= 33_000


@pytest.mark.quick
def test_allocate_budget_includes_price_buckets() -> None:
    report = _report(
        [
            _rec("AAA", "Large Cap", buy=100.0, confidence=80.0),
            _rec("BBB", "Mid Cap", buy=100.0, confidence=70.0),
            _rec("CCC", "Small Cap", buy=100.0, confidence=60.0),
        ]
    )
    report.price_bucket_recommendations = {
        "Below ₹100": [
            _rec("SUZLON", "Small Cap", buy=50.0, confidence=65.0, price_bucket="Below ₹100"),
            _rec("IRFC", "Mid Cap", buy=60.0, confidence=60.0, price_bucket="Below ₹100"),
        ],
    }
    alloc = allocate_budget(report, 50_000.0, tier_budget_split_pct=33.33)

    symbols = {line.symbol for line in alloc.lines}
    assert {"AAA", "BBB", "CCC", "SUZLON", "IRFC"}.issubset(symbols)
    bucket_lines = [line for line in alloc.lines if line.symbol in {"SUZLON", "IRFC"}]
    assert all(line.cap_tier == "Below ₹100" for line in bucket_lines)
    assert alloc.total_invested > 0
    assert alloc.cash_remaining >= 0


@pytest.mark.quick
def test_recommendation_investment_dataframe_merges_positions() -> None:
    from decimal import Decimal

    from app.schemas import PositionOut
    from ui.recommendations_display import recommendation_investment_dataframe

    report = _report([_rec("INFY", "Large Cap", buy=1800.0)])
    alloc = allocate_budget(report, 50_000.0, tier_budget_split_pct=33.33)
    positions = [
        PositionOut(
            symbol="INFY",
            name="Infosys",
            quantity=5,
            avg_cost=Decimal("1800"),
            mark_price=Decimal("1850"),
            market_value=Decimal("9250"),
            unrealized_pnl=Decimal("250"),
        )
    ]
    df = recommendation_investment_dataframe(alloc, positions)
    assert len(df) == 1
    assert df.iloc[0]["Stock"] == "INFY"
    assert df.iloc[0]["Shares (held)"] == 5
    assert df.iloc[0]["Plan status"] in {"Partial", "Open (filled)"}


@pytest.mark.quick
def test_allocate_budget_skips_invalid_and_backfills_same_tier() -> None:
    bad = _rec("CHOLAFIN", "Mid Cap", buy=1888.2, confidence=75.0)
    bad.actual_sell_price = 1888.2
    alt = _rec("HUDCO", "Mid Cap", buy=195.1, confidence=70.0)
    report = _report(
        [
            bad,
            _rec("AAA", "Large Cap"),
            _rec("BBB", "Small Cap"),
        ]
    )
    report.price_bucket_recommendations = {"Mid cap alt": [alt]}

    alloc = allocate_budget(report, 50_000.0, tier_budget_split_pct=33.33)

    assert "CHOLAFIN" not in {line.symbol for line in alloc.lines}
    assert "CHOLAFIN" in alloc.skipped_invalid
    assert "HUDCO" in alloc.backfilled_symbols
    assert "HUDCO" in {line.symbol for line in alloc.lines}


@pytest.mark.quick
def test_allocate_budget_skips_negative_net_lines() -> None:
    tight = _rec("TINY", "Large Cap", buy=500.0, confidence=80.0)
    tight.actual_sell_price = 502.0
    good = _rec("GOOD", "Mid Cap", buy=100.0, confidence=75.0)
    good.actual_sell_price = 115.0
    report = _report([tight, _rec("BBB", "Small Cap", buy=50.0), good])
    alloc = allocate_budget(report, 50_000.0, tier_budget_split_pct=33.33)

    symbols = {line.symbol for line in alloc.lines}
    assert "TINY" not in symbols
    assert all(line.net_profit_after_tax > 0 for line in alloc.lines)


@pytest.mark.quick
def test_allocation_trading_blocked_when_expected_profit_negative() -> None:
    from app.services.budget_allocator import allocation_trading_blocked

    alloc = allocate_budget(_report([]), 50_000.0)
    blocked, reason = allocation_trading_blocked(alloc)
    assert blocked is False

    negative = BudgetAllocationReport(
        budget_inr=50_000,
        total_invested=40_000,
        cash_remaining=10_000,
        expected_profit=-100,
        expected_return_pct=-0.2,
        total_gross_profit=200,
        total_charges=400,
        total_stcg_tax=0,
        total_net_profit_after_tax=-200,
        max_portfolio_loss=2000,
        lines=[_allocate_line_from_rec(_rec("X", "Large Cap", buy=100.0))],
    )
    blocked, reason = allocation_trading_blocked(negative)
    assert blocked is True
    assert reason


def _allocate_line_from_rec(rec: StockRecommendation):
    from app.services.budget_allocator import _allocate_line

    line = _allocate_line(rec, 10_000, 50_000)
    assert line is not None
    return line
