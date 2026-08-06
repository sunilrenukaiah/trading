"""Indian equity delivery trade charges and net profit after tax."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.defaults import (
    DEFAULT_BROKERAGE_MIN_PER_SHARE_INR,
    DEFAULT_EXCHANGE_TXN_RATE,
    DEFAULT_GST_RATE,
    DEFAULT_SEBI_TURNOVER_RATE,
)
from app.services.applicable_rates import get_applicable_rates
from app.services.broker_delivery_profiles import BrokerDeliveryProfile


@dataclass
class SellTargets:
    """Model max target (capped) and conservative actual exit."""

    buy_price: float
    model_sell_price: float
    actual_sell_price: float
    model_profit_pct: float
    actual_profit_pct: float
    model_profit_inr: float
    actual_profit_inr: float


@dataclass
class NetProfitBreakdown:
    shares: int
    buy_price: float
    sell_price: float
    gross_profit: float
    stt_charges: float
    stamp_duty: float
    brokerage: float
    exchange_charges: float
    sebi_charges: float
    gst_charges: float
    dp_charges: float
    total_charges: float
    profit_before_tax: float
    stcg_tax: float
    net_profit_after_tax: float


@dataclass
class RealizedPnlAfterTaxSummary:
    gross_realized_pnl: float
    total_charges: float
    total_stcg_tax: float
    net_after_tax: float
    total_stt: float = 0.0
    total_stamp_duty: float = 0.0
    total_brokerage: float = 0.0
    total_exchange_sebi_gst: float = 0.0
    total_dp_charges: float = 0.0

    @property
    def total_tax_and_charges(self) -> float:
        return round(self.total_charges + self.total_stcg_tax, 2)


@dataclass
class DualBrokerRealizedPnlSummary:
    sharekhan: RealizedPnlAfterTaxSummary
    zerodha: RealizedPnlAfterTaxSummary

    @classmethod
    def empty(cls) -> DualBrokerRealizedPnlSummary:
        zero = RealizedPnlAfterTaxSummary(0.0, 0.0, 0.0, 0.0)
        return cls(sharekhan=zero, zerodha=zero)


def summarize_sell_trades_after_tax(
    sells: list[tuple[int, float, float]],
    *,
    profile: BrokerDeliveryProfile | None = None,
) -> RealizedPnlAfterTaxSummary:
    """Aggregate after-tax P&L from closed sells as (quantity, buy_price, sell_price)."""
    gross = 0.0
    charges = 0.0
    stt = 0.0
    stamp = 0.0
    brokerage = 0.0
    exchange_sebi_gst = 0.0
    dp = 0.0
    stcg = 0.0
    net = 0.0
    for qty, buy_price, sell_price in sells:
        if qty <= 0:
            continue
        row = compute_net_profit(qty, buy_price, sell_price, profile=profile)
        gross += row.gross_profit
        charges += row.total_charges
        stt += row.stt_charges
        stamp += row.stamp_duty
        brokerage += row.brokerage
        exchange_sebi_gst += row.exchange_charges + row.sebi_charges + row.gst_charges
        dp += row.dp_charges
        stcg += row.stcg_tax
        net += row.net_profit_after_tax
    return RealizedPnlAfterTaxSummary(
        gross_realized_pnl=round(gross, 2),
        total_charges=round(charges, 2),
        total_stcg_tax=round(stcg, 2),
        net_after_tax=round(net, 2),
        total_stt=round(stt, 2),
        total_stamp_duty=round(stamp, 2),
        total_brokerage=round(brokerage, 2),
        total_exchange_sebi_gst=round(exchange_sebi_gst, 2),
        total_dp_charges=round(dp, 2),
    )


def summarize_sell_trades_dual_broker(
    sells: list[tuple[int, float, float]],
) -> DualBrokerRealizedPnlSummary:
    from app.services.broker_delivery_profiles import (
        SHAREKHAN_DELIVERY,
        ZERODHA_DELIVERY,
    )

    if not sells:
        return DualBrokerRealizedPnlSummary.empty()
    return DualBrokerRealizedPnlSummary(
        sharekhan=summarize_sell_trades_after_tax(sells, profile=SHAREKHAN_DELIVERY),
        zerodha=summarize_sell_trades_after_tax(sells, profile=ZERODHA_DELIVERY),
    )


def compute_sell_targets(
    buy_price: float,
    raw_target: float,
    *,
    max_profit_pct: float | None = None,
    conservative_ratio: float | None = None,
) -> SellTargets:
    """
    Cap model target at max_profit_pct (default 20%).
    Actual sell = buy + conservative_ratio × (model target profit).
    Example: buy ₹100, model ₹120 (20%) → actual ₹110 (10%).
    """
    from app.defaults import DEFAULT_MIN_TARGET_PROFIT_PCT

    max_pct = max_profit_pct if max_profit_pct is not None else settings.max_target_profit_pct
    ratio = (
        conservative_ratio
        if conservative_ratio is not None
        else get_applicable_rates().conservative_exit_ratio
    )

    max_sell = round(buy_price * (1 + max_pct / 100), 2)
    model_sell = round(min(raw_target, max_sell), 2)
    if model_sell < buy_price:
        model_sell = buy_price

    model_profit = model_sell - buy_price
    actual_profit = round(model_profit * ratio, 2)
    actual_sell = round(buy_price + actual_profit, 2)

    min_sell = round(buy_price * (1 + DEFAULT_MIN_TARGET_PROFIT_PCT / 100), 2)
    if model_profit > 0 and actual_sell <= buy_price:
        actual_profit = round(max(min_sell - buy_price, actual_profit), 2)
        actual_sell = round(buy_price + actual_profit, 2)
    if actual_sell <= buy_price and model_sell > buy_price:
        actual_sell = min_sell
        actual_profit = round(actual_sell - buy_price, 2)

    model_pct = round(model_profit / buy_price * 100, 2) if buy_price else 0.0
    actual_pct = round(actual_profit / buy_price * 100, 2) if buy_price else 0.0

    return SellTargets(
        buy_price=buy_price,
        model_sell_price=model_sell,
        actual_sell_price=actual_sell,
        model_profit_pct=model_pct,
        actual_profit_pct=actual_pct,
        model_profit_inr=round(model_profit, 2),
        actual_profit_inr=actual_profit,
    )


def _side_brokerage(
    shares: int,
    trade_value: float,
    *,
    rate: float,
    min_per_share_inr: float,
) -> float:
    """Delivery equity per side: max(rate × value, min ₹/share); zero rate → no fee."""
    if rate <= 0 and min_per_share_inr <= 0:
        return 0.0
    return round(max(trade_value * rate, shares * min_per_share_inr), 2)


def compute_net_profit(
    shares: int,
    buy_price: float,
    sell_price: float,
    *,
    profile: BrokerDeliveryProfile | None = None,
    stcg_rate: float | None = None,
    stt_rate: float | None = None,
    stamp_duty_rate: float | None = None,
    brokerage_rate: float | None = None,
    brokerage_min_per_share_inr: float | None = None,
    exchange_txn_rate: float | None = None,
    sebi_turnover_rate: float | None = None,
    gst_rate: float | None = None,
) -> NetProfitBreakdown:
    """
    Estimate net profit for delivery equity (short-term).

    Uses an explicit broker profile when given; otherwise active applicable_rates
    (Sharekhan-aligned defaults).
    """
    if profile is not None:
        stcg = profile.stcg_tax_rate
        stt = profile.stt_rate
        stamp = profile.stamp_duty_rate
        brokerage = profile.brokerage_rate
        min_per_share = profile.brokerage_min_per_share_inr
        exchange = profile.exchange_txn_rate
        sebi = profile.sebi_turnover_rate
        gst = profile.gst_rate
        dp_per_scrip = profile.dp_charge_per_scrip_inr
    else:
        rates = get_applicable_rates()
        stcg = stcg_rate if stcg_rate is not None else rates.stcg_tax_rate
        stt = stt_rate if stt_rate is not None else rates.stt_rate
        stamp = stamp_duty_rate if stamp_duty_rate is not None else rates.stamp_duty_rate
        brokerage = brokerage_rate if brokerage_rate is not None else rates.brokerage_rate
        min_per_share = (
            brokerage_min_per_share_inr
            if brokerage_min_per_share_inr is not None
            else getattr(rates, "brokerage_min_per_share_inr", DEFAULT_BROKERAGE_MIN_PER_SHARE_INR)
        )
        exchange = (
            exchange_txn_rate
            if exchange_txn_rate is not None
            else getattr(rates, "exchange_txn_rate", DEFAULT_EXCHANGE_TXN_RATE)
        )
        sebi = (
            sebi_turnover_rate
            if sebi_turnover_rate is not None
            else getattr(rates, "sebi_turnover_rate", DEFAULT_SEBI_TURNOVER_RATE)
        )
        gst = gst_rate if gst_rate is not None else getattr(rates, "gst_rate", DEFAULT_GST_RATE)
        dp_per_scrip = getattr(rates, "dp_charge_per_scrip_inr", 0.0)

    buy_value = shares * buy_price
    sell_value = shares * sell_price
    gross = round(sell_value - buy_value, 2)
    turnover = buy_value + sell_value

    stt_charges = round((buy_value + sell_value) * stt, 2)
    stamp_duty = round(buy_value * stamp, 2)
    buy_brokerage = _side_brokerage(
        shares, buy_value, rate=brokerage, min_per_share_inr=min_per_share
    )
    sell_brokerage = _side_brokerage(
        shares, sell_value, rate=brokerage, min_per_share_inr=min_per_share
    )
    brokerage_fee = round(buy_brokerage + sell_brokerage, 2)
    exchange_charges = round(turnover * exchange, 2)
    sebi_charges = round(turnover * sebi, 2)
    gst_charges = round((brokerage_fee + exchange_charges + sebi_charges) * gst, 2)
    dp_charges = round(dp_per_scrip, 2) if dp_per_scrip > 0 else 0.0
    total_charges = round(
        stt_charges
        + stamp_duty
        + brokerage_fee
        + exchange_charges
        + sebi_charges
        + gst_charges
        + dp_charges,
        2,
    )

    profit_before_tax = round(gross - total_charges, 2)
    stcg_tax = round(max(0.0, profit_before_tax) * stcg, 2)
    net = round(profit_before_tax - stcg_tax, 2)

    return NetProfitBreakdown(
        shares=shares,
        buy_price=buy_price,
        sell_price=sell_price,
        gross_profit=gross,
        stt_charges=stt_charges,
        stamp_duty=stamp_duty,
        brokerage=brokerage_fee,
        exchange_charges=exchange_charges,
        sebi_charges=sebi_charges,
        gst_charges=gst_charges,
        dp_charges=dp_charges,
        total_charges=total_charges,
        profit_before_tax=profit_before_tax,
        stcg_tax=stcg_tax,
        net_profit_after_tax=net,
    )
