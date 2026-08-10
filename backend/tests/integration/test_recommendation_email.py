"""Recommendation evening email formatting."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.budget_allocator import AllocationLine, BudgetAllocationReport
from app.services.recommendation_email import build_recommendation_email, email_configured
from app.services.recommendation_engine import RecommendationReport


def _sample_report() -> RecommendationReport:
    return RecommendationReport(
        generated_at=date(2026, 8, 10),
        prediction_date=date(2026, 8, 11),
        data_through_date=date(2026, 8, 10),
        lookback_days=30,
        eval_days=30,
        top_patterns=[],
        recommendations=[],
        tier_counts={},
        notes=[],
        max_target_profit_pct=5.0,
    )


def _sample_allocation() -> BudgetAllocationReport:
    line = AllocationLine(
        symbol="RELIANCE",
        cap_tier="Large Cap",
        shares=2,
        buy_price=1400.0,
        investment=2800.0,
        stop_loss=1350.0,
        model_target_price=1450.0,
        actual_sell_price=1440.0,
        expected_profit=80.0,
        gross_profit=100.0,
        profit_before_tax=90.0,
        total_charges=5.0,
        stcg_tax=5.0,
        net_profit_after_tax=80.0,
        max_loss=100.0,
        weight_pct=50.0,
        pattern_name="Demo pattern",
        confidence_score=70.0,
    )
    return BudgetAllocationReport(
        budget_inr=50_000.0,
        total_invested=2800.0,
        cash_remaining=47_200.0,
        expected_profit=80.0,
        expected_return_pct=0.16,
        total_gross_profit=100.0,
        total_charges=5.0,
        total_stcg_tax=5.0,
        total_net_profit_after_tax=80.0,
        max_portfolio_loss=100.0,
        lines=[line],
    )


@pytest.mark.quick
def test_build_recommendation_email_includes_symbol_and_date() -> None:
    subject, text, html = build_recommendation_email(_sample_report(), _sample_allocation())
    assert "11 Aug 2026" in subject
    assert "RELIANCE" in text
    assert "RELIANCE" in html
    assert "next session" in html.lower() or "next session" in text.lower() or "plan for" in text.lower()


@pytest.mark.quick
def test_email_configured_false_without_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "smtp_username", None)
    monkeypatch.setattr(settings, "smtp_password", None)
    assert email_configured() is False
