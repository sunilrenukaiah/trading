from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, configure_mappers, mapped_column, relationship

from app.models.base import Base


class InstrumentType(str, enum.Enum):
    INDEX = "INDEX"
    EQUITY = "EQUITY"


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class TradePlanStatus(str, enum.Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    TIME_EXIT = "TIME_EXIT"
    CANCELLED = "CANCELLED"


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), default="NSE")
    instrument_type: Mapped[InstrumentType] = mapped_column(
        Enum(InstrumentType, name="instrument_type"), nullable=False
    )
    yfinance_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    sharekhan_scrip_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_nifty50: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    candles: Mapped[list["OhlcvCandle"]] = relationship(back_populates="instrument")
    orders: Mapped[list["PaperOrder"]] = relationship(back_populates="instrument")
    positions: Mapped[list["PaperPosition"]] = relationship(back_populates="instrument")


class OhlcvCandle(Base):
    __tablename__ = "ohlcv_candles"
    __table_args__ = (
        UniqueConstraint("instrument_id", "trade_date", name="uq_candle_instrument_date"),
        Index("ix_candles_instrument_date", "instrument_id", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="yfinance")
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    instrument: Mapped["Instrument"] = relationship(back_populates="candles")


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="Default")
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    orders: Mapped[list["PaperOrder"]] = relationship(back_populates="account")
    positions: Mapped[list["PaperPosition"]] = relationship(back_populates="account")
    trades: Mapped[list["PaperTrade"]] = relationship(back_populates="account")
    trade_plans: Mapped[list["PaperTradePlan"]] = relationship(back_populates="account")


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, name="order_side"), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(
        Enum(OrderType, name="order_type"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status"), default=OrderStatus.PENDING
    )
    filled_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    account: Mapped["PaperAccount"] = relationship(back_populates="orders")
    instrument: Mapped["Instrument"] = relationship(back_populates="orders")
    trade: Mapped["PaperTrade | None"] = relationship(back_populates="order", uselist=False)


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        UniqueConstraint("account_id", "instrument_id", name="uq_position_account_instrument"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)

    account: Mapped["PaperAccount"] = relationship(back_populates="positions")
    instrument: Mapped["Instrument"] = relationship(back_populates="positions")


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"), unique=True, nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide, name="trade_side"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    account: Mapped["PaperAccount"] = relationship(back_populates="trades")
    order: Mapped["PaperOrder"] = relationship(back_populates="trade")


class PaperTradePlan(Base):
    __tablename__ = "paper_trade_plans"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "instrument_id",
            "recommendation_date",
            name="uq_trade_plan_day_symbol",
        ),
        Index("ix_trade_plans_status_rec_date", "status", "recommendation_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    recommendation_date: Mapped[date] = mapped_column(Date, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_limit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    stop_loss_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    status: Mapped[TradePlanStatus] = mapped_column(
        Enum(TradePlanStatus, name="trade_plan_status"),
        default=TradePlanStatus.PENDING_ENTRY,
        nullable=False,
    )
    entry_order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"), nullable=True)
    exit_order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"), nullable=True)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    pattern_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped["PaperAccount"] = relationship(back_populates="trade_plans")
    instrument: Mapped["Instrument"] = relationship()


class RecommendationSnapshot(Base):
    __tablename__ = "recommendation_snapshots"
    __table_args__ = (
        UniqueConstraint("analysis_date", name="uq_recommendation_snapshot_analysis_date"),
        Index("ix_recommendation_snapshots_prediction_date", "prediction_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_date: Mapped[date] = mapped_column(Date, nullable=False)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    budget_inr: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    max_target_profit_pct: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    eval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_count: Mapped[int] = mapped_column(Integer, nullable=False)
    universe: Mapped[str] = mapped_column(String(32), default="NIFTY250", nullable=False)
    simulation_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    pattern_scores: Mapped[list["BacktestPatternScore"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    stock_scores: Mapped[list["BacktestStockScore"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class BacktestPatternScore(Base):
    __tablename__ = "backtest_pattern_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), nullable=False)
    pattern_id: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern_name: Mapped[str] = mapped_column(String(128), nullable=False)
    total_correct: Mapped[int] = mapped_column(Integer, default=0)
    total_signals: Mapped[int] = mapped_column(Integer, default=0)
    avg_daily_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    overall_hit_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    rank: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped["BacktestRun"] = relationship(back_populates="pattern_scores")


class BacktestStockScore(Base):
    __tablename__ = "backtest_stock_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), nullable=False)
    pattern_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    signals: Mapped[int] = mapped_column(Integer, default=0)
    hit_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)

    run: Mapped["BacktestRun"] = relationship(back_populates="stock_scores")


from app.models.audit_log import AuditLog  # noqa: E402, F401

configure_mappers()
