# Import all patterns to register them with the pattern registry.
from app.strategies.patterns import (
    bollinger,
    candlestick,
    chart_patterns,
    combinations,
    fidelity_candlestick,
    fidelity_indicators,
    groww_candlestick,
    price_action,
    technical,
)

__all__ = [
    "bollinger",
    "candlestick",
    "chart_patterns",
    "combinations",
    "fidelity_candlestick",
    "fidelity_indicators",
    "groww_candlestick",
    "price_action",
    "technical",
]
