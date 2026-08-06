import enum
from abc import ABC, abstractmethod

import pandas as pd


class Signal(str, enum.Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Pattern(ABC):
    id: str
    name: str
    lookback_days: int = 20

    @abstractmethod
    def evaluate(self, candles: pd.DataFrame) -> Signal:
        """Evaluate pattern on OHLCV ending the day before the evaluation day."""
