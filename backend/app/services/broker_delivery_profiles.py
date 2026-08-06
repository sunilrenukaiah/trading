"""Equity delivery charge profiles for broker comparison (NSE delivery)."""

from __future__ import annotations

from dataclasses import dataclass

from app.defaults import (
    DEFAULT_GST_RATE,
    DEFAULT_SEBI_TURNOVER_RATE,
    DEFAULT_STAMP_DUTY_RATE,
    DEFAULT_STCG_TAX_RATE,
    DEFAULT_STT_RATE,
    DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR,
)

# Sharekhan: https://www.sharekhan.com/pricing
# Zerodha: https://zerodha.com/charges/#tab-equities


@dataclass(frozen=True)
class BrokerDeliveryProfile:
    broker_id: str
    label: str
    brokerage_rate: float
    brokerage_min_per_share_inr: float
    exchange_txn_rate: float
    stt_rate: float = DEFAULT_STT_RATE
    stamp_duty_rate: float = DEFAULT_STAMP_DUTY_RATE
    sebi_turnover_rate: float = DEFAULT_SEBI_TURNOVER_RATE
    gst_rate: float = DEFAULT_GST_RATE
    stcg_tax_rate: float = DEFAULT_STCG_TAX_RATE
    dp_charge_per_scrip_inr: float = 0.0


SHAREKHAN_DELIVERY = BrokerDeliveryProfile(
    broker_id="sharekhan",
    label="Sharekhan",
    brokerage_rate=0.003,
    brokerage_min_per_share_inr=0.01,
    exchange_txn_rate=0.0000297,
    dp_charge_per_scrip_inr=0.0,
)

ZERODHA_DELIVERY = BrokerDeliveryProfile(
    broker_id="zerodha",
    label="Zerodha",
    brokerage_rate=0.0,
    brokerage_min_per_share_inr=0.0,
    exchange_txn_rate=0.0000307,
    dp_charge_per_scrip_inr=DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR,
)

BROKER_DELIVERY_PROFILES: tuple[BrokerDeliveryProfile, ...] = (
    SHAREKHAN_DELIVERY,
    ZERODHA_DELIVERY,
)
