from pydantic_settings import BaseSettings, SettingsConfigDict

from app.defaults import (
    DEFAULT_BROKERAGE_RATE,
    DEFAULT_CONSERVATIVE_EXIT_RATIO,
    DEFAULT_DAILY_TRADING_BUDGET_INR,
    DEFAULT_MARKET_DATA_UNIVERSE,
    DEFAULT_MAX_TARGET_PROFIT_PCT,
    DEFAULT_PAPER_TRADING_RETENTION_DAYS,
    DEFAULT_SIMULATION_UNIVERSE,
    DEFAULT_STAMP_DUTY_RATE,
    DEFAULT_STCG_TAX_RATE,
    DEFAULT_STT_RATE,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://trading:trading@localhost:5432/trading"
    paper_starting_cash: float = DEFAULT_DAILY_TRADING_BUDGET_INR
    data_provider: str = "nse"
    backfill_days: int = 120
    market_data_universe: str = DEFAULT_MARKET_DATA_UNIVERSE
    default_simulation_universe: str = DEFAULT_SIMULATION_UNIVERSE
    max_target_profit_pct: float = DEFAULT_MAX_TARGET_PROFIT_PCT
    paper_trading_retention_days: int = DEFAULT_PAPER_TRADING_RETENTION_DAYS
    conservative_exit_ratio: float = DEFAULT_CONSERVATIVE_EXIT_RATIO
    stcg_tax_rate: float = DEFAULT_STCG_TAX_RATE
    stt_rate: float = DEFAULT_STT_RATE
    stamp_duty_rate: float = DEFAULT_STAMP_DUTY_RATE
    brokerage_rate: float = DEFAULT_BROKERAGE_RATE

    daily_trading_budget_inr: float = DEFAULT_DAILY_TRADING_BUDGET_INR

    audit_enabled: bool = True
    audit_log_api_requests: bool = True
    audit_traceback_max_chars: int = 4000
    audit_backend: str = "composite"
    audit_blocking: bool = False
    audit_capture_log_errors: bool = True
    audit_capture_unhandled_exceptions: bool = True

    sharekhan_api_key: str | None = None
    sharekhan_customer_id: str | None = None
    sharekhan_access_token: str | None = None

    lab_mode: bool = False
    lab_schema: str | None = None


settings = Settings()
