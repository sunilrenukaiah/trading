"""Recommendation table formatting."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.recommendation_engine import (
    PatternRanking,
    RecommendationReport,
    StockRecommendation,
)
from ui.recommendations_display import (
    allocation_simulation_dataframe,
    budget_simulation_comparison_dataframe,
    format_sell_target_display,
    report_recommendations_dataframe,
)


def _sample_report(max_target: float = 25.0) -> RecommendationReport:
    return RecommendationReport(
        generated_at=date.today(),
        prediction_date=date.today(),
        data_through_date=date.today(),
        lookback_days=20,
        eval_days=30,
        top_patterns=[PatternRanking("p1", "Hammer", 60.0, 6, 10, 0.6)],
        recommendations=[
            StockRecommendation(
                symbol="INFY",
                cap_tier="Large Cap",
                pattern_id="p1",
                pattern_name="Hammer",
                pattern_hit_rate_30d=60.0,
                signal="BULLISH",
                action="BUY",
                buy_price=100.0,
                stop_loss=95.0,
                resistance=110.0,
                sell_price=120.0,
                actual_sell_price=110.0,
                model_profit_pct=20.0,
                actual_profit_pct=10.0,
                risk_reward=2.0,
                latest_close=100.0,
                prev_close=99.0,
                expected_move_pct=10.0,
                confidence_score=70.0,
            )
        ],
        tier_counts={"large_cap": 1},
        max_target_profit_pct=max_target,
    )


@pytest.mark.quick
def test_format_sell_target_display_shows_actual_and_model_prices() -> None:
    assert format_sell_target_display(282.0, 312.0) == "₹282.00 (₹312.00)"
    assert format_sell_target_display(194.19, 195.20) == "₹194.19 (₹195.20)"


@pytest.mark.quick
def test_recommendations_dataframe_includes_profit_columns() -> None:
    df = report_recommendations_dataframe(_sample_report())
    assert "Profit before tax (1 sh)" in df.columns
    assert "Profit after tax (1 sh)" in df.columns
    assert "Model target (max 25%)" in df.columns
    assert df.iloc[0]["Profit before tax (1 sh)"].startswith("₹")
    assert df.iloc[0]["Profit after tax (1 sh)"].startswith("₹")


@pytest.mark.quick
def test_allocation_simulation_dataframe_includes_stop_and_sell_target() -> None:
    from app.services.budget_allocator import allocate_budget

    report = _sample_report()
    allocation = allocate_budget(report, 10_000.0)
    df = allocation_simulation_dataframe(allocation)
    assert "Stop loss" in df.columns
    assert "Sell target" in df.columns
    assert df.iloc[0]["Stop loss"].startswith("₹")
    assert "(" in df.iloc[0]["Sell target"]


@pytest.mark.quick
def test_budget_simulation_comparison_dataframe_scales_shares() -> None:
    report = _sample_report()
    df = budget_simulation_comparison_dataframe(report, [10_000.0, 50_000.0])
    assert list(df.columns) == ["Stock", "₹10,000", "₹50,000"]
    assert df.iloc[0]["Stock"] == "INFY"
    low = int(df.iloc[0]["₹10,000"])
    high = int(df.iloc[0]["₹50,000"])
    assert high >= low
