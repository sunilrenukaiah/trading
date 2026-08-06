"""Trade tax and after-tax realized P&L summaries."""

from __future__ import annotations

import pytest

from app.services.trade_tax import compute_net_profit, summarize_sell_trades_after_tax


@pytest.mark.quick
def test_summarize_sell_trades_after_tax() -> None:
    single = compute_net_profit(10, 100.0, 110.0)
    summary = summarize_sell_trades_after_tax([(10, 100.0, 110.0)])
    assert summary.gross_realized_pnl == single.gross_profit
    assert summary.total_charges == single.total_charges
    assert summary.total_stcg_tax == single.stcg_tax
    assert summary.net_after_tax == single.net_profit_after_tax
    assert summary.total_tax_and_charges == round(
        single.total_charges + single.stcg_tax, 2
    )


@pytest.mark.quick
def test_summarize_sell_trades_empty() -> None:
    summary = summarize_sell_trades_after_tax([])
    assert summary.net_after_tax == 0.0


@pytest.mark.quick
def test_dual_broker_dp_and_delivery_charges() -> None:
    from app.services.broker_delivery_profiles import ZERODHA_DELIVERY
    from app.services.trade_tax import compute_net_profit, summarize_sell_trades_dual_broker

    row = compute_net_profit(10, 500.0, 520.0, profile=ZERODHA_DELIVERY)
    assert row.dp_charges == pytest.approx(15.34)
    assert row.brokerage == 0.0

    dual = summarize_sell_trades_dual_broker([(10, 100.0, 110.0), (5, 200.0, 210.0)])
    assert dual.sharekhan.total_dp_charges == 0.0
    assert dual.zerodha.total_dp_charges == pytest.approx(30.68, abs=0.01)

    large = summarize_sell_trades_dual_broker([(20, 500.0, 550.0)])
    assert large.zerodha.total_dp_charges == pytest.approx(15.34)
    assert large.zerodha.net_after_tax > large.sharekhan.net_after_tax


@pytest.mark.quick
def test_compute_sell_targets_floors_actual_when_model_has_upside() -> None:
    from app.services.trade_tax import compute_sell_targets

    # Tiny model profit that would round to zero actual at 50% conservative ratio.
    targets = compute_sell_targets(1888.2, 1888.3, conservative_ratio=0.5)
    assert targets.actual_sell_price > targets.buy_price
    assert targets.model_sell_price > targets.buy_price


@pytest.mark.quick
def test_compute_sell_targets_cholafin_flat_model_stays_at_entry() -> None:
    from app.services.trade_tax import compute_sell_targets

    targets = compute_sell_targets(1888.2, 1888.2)
    assert targets.actual_sell_price == targets.buy_price
    assert targets.model_sell_price == targets.buy_price
