"""App-wide default constants (safe to import from UI without Settings cache issues)."""

DEFAULT_SIMULATION_UNIVERSE = "NIFTY250"
DEFAULT_MARKET_DATA_UNIVERSE = "NIFTY250"
DEFAULT_DAILY_TRADING_BUDGET_INR = 50_000.0
DEFAULT_MAX_TARGET_PROFIT_PCT = 80.0
DEFAULT_PAPER_TRADING_RETENTION_DAYS = 30
DEFAULT_STCG_TAX_RATE = 0.20
DEFAULT_STT_RATE = 0.001
DEFAULT_STAMP_DUTY_RATE = 0.00015
# Mirae Asset Sharekhan equity delivery: 0.30% per side (min ₹0.01/share).
# https://www.sharekhan.com/pricing
DEFAULT_BROKERAGE_RATE = 0.003
DEFAULT_BROKERAGE_MIN_PER_SHARE_INR = 0.01
# NSE equity delivery exchange txn charge (per side, applied to buy + sell turnover).
DEFAULT_EXCHANGE_TXN_RATE = 0.0000297
# SEBI turnover fee ₹10/crore.
DEFAULT_SEBI_TURNOVER_RATE = 1e-7
DEFAULT_GST_RATE = 0.18
DEFAULT_CONSERVATIVE_EXIT_RATIO = 0.5
# Minimum bracket target above entry when model shows any upside (avoids target == entry).
DEFAULT_MIN_TARGET_PROFIT_PCT = 0.25
# Intraday-friendly target: ATR fraction for minimum raw target above entry (was 0.5).
DEFAULT_TARGET_ATR_MULTIPLIER = 0.35
# Resistance cap for raw target (was 0.98).
DEFAULT_TARGET_RESISTANCE_FACTOR = 0.85
# Default model target cap when UI does not override (tighter for same-day square-off).
DEFAULT_RECOMMENDATION_MAX_TARGET_PROFIT_PCT = 50.0
# Minimum per-share expected move (actual sell − buy) for a recommendation pick.
DEFAULT_MIN_EXPECTED_MOVE_INR = 1.0
# Reference ₹ slice when estimating charges for a typical tier/bucket pick.
DEFAULT_REFERENCE_ALLOCATION_INR = 5500.0
# Pick must show at least this net profit after tax at the reference size.
DEFAULT_MIN_NET_PROFIT_AFTER_TAX_INR = 1.0
# Latest session volume must be at least this fraction of the N-day average.
DEFAULT_MIN_RELATIVE_VOLUME = 0.75
DEFAULT_VOLUME_LOOKBACK_DAYS = 20
DEFAULT_BROKER_PROFILE = "sharekhan_delivery"
# Sharekhan: nil DP debit on sell via broker. Zerodha: ₹15.34/scrip on sell.
DEFAULT_DP_CHARGE_PER_SCRIP_INR = 0.0
DEFAULT_ZERODHA_DP_CHARGE_PER_SCRIP_INR = 15.34
# Re-run full bracket catch-up (NSE day OHLC + live quotes) after this many minutes.
DEFAULT_BRACKET_RECONCILE_STALE_MINUTES = 5
