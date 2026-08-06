from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PatternInfo(BaseModel):
    id: str
    name: str
    lookback_days: int


class PatternScoreOut(BaseModel):
    pattern_id: str
    pattern_name: str
    total_correct: int
    total_signals: int
    avg_daily_score: Decimal
    overall_hit_rate: Decimal
    rank: int
    avg_display: str


class StockScoreOut(BaseModel):
    symbol: str
    correct: int
    signals: int
    hit_rate: Decimal

    model_config = {"from_attributes": True}


class BacktestRunOut(BaseModel):
    id: int
    run_at: datetime
    eval_days: int
    lookback_days: int
    stock_count: int
    patterns: list[PatternScoreOut]


class DayDetailOut(BaseModel):
    trade_date: str
    symbol: str
    signal: str
    actual: str
    correct: bool
    prev_close: float
    predicted_close: float
    actual_close: float
    predicted_change_pct: float
    actual_change_pct: float
    price_error_pct: float
