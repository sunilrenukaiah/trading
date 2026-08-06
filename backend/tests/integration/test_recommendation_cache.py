"""Recommendation cache serialize/deserialize roundtrip."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.budget_allocator import AllocationLine, BudgetAllocationReport
from app.services.recommendation_cache import deserialize_snapshot, serialize_snapshot
from app.services.recommendation_engine import (
    PatternRanking,
    RecommendationReport,
    StockRecommendation,
)


def _sample_snapshot() -> tuple[RecommendationReport, BudgetAllocationReport, float, float]:
    report = RecommendationReport(
        generated_at=date(2026, 7, 29),
        prediction_date=date(2026, 7, 30),
        data_through_date=date(2026, 7, 29),
        lookback_days=30,
        eval_days=15,
        top_patterns=[
            PatternRanking(
                pattern_id="doji",
                pattern_name="Doji",
                hit_rate_pct=60.0,
                total_correct=6,
                total_signals=10,
                avg_daily_score=0.6,
            )
        ],
        recommendations=[
            StockRecommendation(
                symbol="RELIANCE",
                cap_tier="Large Cap",
                pattern_id="doji",
                pattern_name="Doji",
                pattern_hit_rate_30d=60.0,
                signal="BULLISH",
                action="BUY",
                buy_price=2500.0,
                stop_loss=2450.0,
                resistance=2550.0,
                sell_price=2600.0,
                actual_sell_price=2600.0,
                model_profit_pct=4.0,
                actual_profit_pct=4.0,
                risk_reward=2.0,
                latest_close=2495.0,
                prev_close=2490.0,
                expected_move_pct=2.0,
                confidence_score=0.8,
                supporting_patterns=["doji"],
            )
        ],
        tier_counts={"Large Cap": 1},
        max_target_profit_pct=20.0,
        notes=["test note"],
    )
    allocation = BudgetAllocationReport(
        budget_inr=100_000.0,
        total_invested=25_000.0,
        cash_remaining=75_000.0,
        expected_profit=500.0,
        expected_return_pct=0.5,
        total_gross_profit=600.0,
        total_charges=50.0,
        total_stcg_tax=50.0,
        total_net_profit_after_tax=500.0,
        max_portfolio_loss=500.0,
        lines=[
            AllocationLine(
                symbol="RELIANCE",
                cap_tier="Large Cap",
                shares=10,
                buy_price=2500.0,
                investment=25_000.0,
                stop_loss=2450.0,
                model_target_price=2600.0,
                actual_sell_price=2600.0,
                expected_profit=500.0,
                gross_profit=600.0,
                profit_before_tax=550.0,
                total_charges=50.0,
                stcg_tax=50.0,
                net_profit_after_tax=500.0,
                max_loss=500.0,
                weight_pct=25.0,
                pattern_name="Doji",
                confidence_score=0.8,
            )
        ],
    )
    return report, allocation, 100_000.0, 20.0


@pytest.mark.quick
def test_recommendation_snapshot_roundtrip() -> None:
    report, allocation, budget, max_target = _sample_snapshot()
    payload = serialize_snapshot(
        report,
        allocation,
        budget_inr=budget,
        max_target_profit_pct=max_target,
    )
    restored_report, restored_alloc, restored_budget, restored_max = deserialize_snapshot(payload)

    assert restored_report.prediction_date == report.prediction_date
    assert restored_report.recommendations[0].symbol == "RELIANCE"
    assert restored_alloc.lines[0].shares == 10
    assert restored_budget == budget
    assert restored_max == max_target


@pytest.mark.quick
def test_deserialize_backfills_legacy_stock_fields() -> None:
    report, allocation, budget, max_target = _sample_snapshot()
    payload = serialize_snapshot(
        report,
        allocation,
        budget_inr=budget,
        max_target_profit_pct=max_target,
    )
    legacy = payload["report"]["recommendations"][0]
    legacy.pop("expected_move_inr", None)
    legacy.pop("relative_volume", None)
    legacy.pop("volume_score", None)

    restored_report, _, _, _ = deserialize_snapshot(payload)
    rec = restored_report.recommendations[0]
    assert rec.expected_move_inr == pytest.approx(100.0)
    assert rec.relative_volume is None
    assert rec.volume_score == 0.0


@pytest.mark.quick
def test_midday_snapshot_save_and_load_today(tmp_path, monkeypatch) -> None:
    from datetime import date

    cache_path = tmp_path / "midday_recommendation_snapshot.json"
    monkeypatch.setattr(
        "app.services.recommendation_cache.MIDDAY_CACHE_PATH",
        cache_path,
    )
    monkeypatch.setattr(
        "app.services.recommendation_cache.today_ist",
        lambda: date(2026, 7, 29),
    )

    from app.services.recommendation_cache import (
        load_midday_cached_recommendations_for_ui,
        save_midday_recommendation_snapshot,
    )

    report, allocation, budget, max_target = _sample_snapshot()
    generated_at = save_midday_recommendation_snapshot(
        report,
        allocation,
        budget_inr=budget,
        max_target_profit_pct=max_target,
        analysis_date=date(2026, 7, 29),
    )

    cached = load_midday_cached_recommendations_for_ui()
    assert cached is not None
    restored_report, restored_alloc, restored_budget, restored_max, cached_at = cached
    assert restored_report.prediction_date == report.prediction_date
    assert restored_alloc.lines[0].symbol == "RELIANCE"
    assert restored_budget == budget
    assert restored_max == max_target
    assert cached_at == generated_at


@pytest.mark.quick
def test_midday_snapshot_ignored_for_other_days(tmp_path, monkeypatch) -> None:
    from datetime import date

    cache_path = tmp_path / "midday_recommendation_snapshot.json"
    monkeypatch.setattr(
        "app.services.recommendation_cache.MIDDAY_CACHE_PATH",
        cache_path,
    )
    monkeypatch.setattr(
        "app.services.recommendation_cache.today_ist",
        lambda: date(2026, 7, 30),
    )

    from app.services.recommendation_cache import (
        load_midday_cached_recommendations_for_ui,
        save_midday_recommendation_snapshot,
    )

    report, allocation, budget, max_target = _sample_snapshot()
    save_midday_recommendation_snapshot(
        report,
        allocation,
        budget_inr=budget,
        max_target_profit_pct=max_target,
        analysis_date=date(2026, 7, 29),
    )

    assert load_midday_cached_recommendations_for_ui() is None
