from pydantic import field_validator
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

    # Evening recommendation summary email (SMTP). Leave unset to skip sending.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None
    email_to: str | None = None
    email_enabled: bool = True

    @field_validator("smtp_host", "smtp_username", "smtp_password", "email_from", "email_to", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("smtp_port", mode="before")
    @classmethod
    def _default_smtp_port(cls, value: object) -> object:
        if value is None or value == "":
            return 587
        return value

    @field_validator("smtp_use_tls", "email_enabled", mode="before")
    @classmethod
    def _empty_bool_default(cls, value: object, info) -> object:
        if value is None or value == "":
            return True if info.field_name == "email_enabled" else True
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value


settings = Settings()
