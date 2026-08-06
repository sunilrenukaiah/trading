from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class InstrumentType(str, Enum):
    INDEX = "INDEX"
    EQUITY = "EQUITY"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionSource(str, Enum):
    RECOMMENDATION = "recommendation"
    MANUAL = "manual"


class InstrumentOut(BaseModel):
    id: int
    symbol: str
    name: str
    exchange: str
    instrument_type: InstrumentType
    is_nifty50: bool

    model_config = {"from_attributes": True}


class CandleOut(BaseModel):
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    model_config = {"from_attributes": True}


class MarketSummaryItem(BaseModel):
    symbol: str
    name: str
    instrument_type: InstrumentType
    last_close: Decimal | None
    prev_close: Decimal | None
    change_pct: float | None


class PlaceOrderRequest(BaseModel):
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int = Field(gt=0)
    limit_price: Decimal | None = None


class OrderOut(BaseModel):
    id: int
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: Decimal | None
    status: OrderStatus
    filled_price: Decimal | None
    filled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PositionOut(BaseModel):
    symbol: str
    name: str
    quantity: int
    avg_cost: Decimal
    mark_price: Decimal | None
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    source: PositionSource = PositionSource.MANUAL


class TradeOut(BaseModel):
    id: int
    symbol: str
    side: OrderSide
    quantity: int
    price: Decimal
    realized_pnl: Decimal
    executed_at: datetime


class AccountOut(BaseModel):
    name: str
    cash_balance: Decimal
    equity_value: Decimal
    total_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    initial_cash: Decimal
