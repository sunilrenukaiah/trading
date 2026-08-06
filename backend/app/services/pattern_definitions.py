"""Pattern metadata catalog — formulas, explanations, and example charts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import plotly.graph_objects as go

from app.services.pattern_examples import build_pattern_example
from app.strategies.registry import get_all_patterns
from ui.pattern_definition_chart import build_pattern_definition_chart

_DEFINITIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "pattern_definitions.json"


@dataclass(frozen=True)
class PatternDefinition:
    pattern_id: str
    name: str
    category: str
    formula: str
    explanation: str
    signal: str
    lookback_days: int


@lru_cache(maxsize=1)
def _load_meta() -> dict[str, dict[str, str]]:
    return json.loads(_DEFINITIONS_PATH.read_text(encoding="utf-8"))


def list_pattern_definitions() -> list[PatternDefinition]:
    """All registered patterns with catalog metadata, sorted by category then name."""
    meta = _load_meta()
    rows: list[PatternDefinition] = []
    for pattern in get_all_patterns():
        entry = meta.get(pattern.id, {})
        rows.append(
            PatternDefinition(
                pattern_id=pattern.id,
                name=pattern.name,
                category=entry.get("category", "Other"),
                formula=entry.get("formula", "See pattern implementation."),
                explanation=entry.get(
                    "explanation",
                    "This pattern is evaluated on daily OHLCV from the local database.",
                ),
                signal=entry.get("signal", "BOTH"),
                lookback_days=pattern.lookback_days,
            )
        )
    return sorted(rows, key=lambda row: (row.category, row.name.lower()))


def pattern_categories() -> list[str]:
    return sorted({row.category for row in list_pattern_definitions()})


def build_pattern_example_chart(pattern_id: str) -> go.Figure:
    """Illustrative candlestick chart for a pattern definition."""
    meta = _load_meta()
    pattern = next((p for p in get_all_patterns() if p.id == pattern_id), None)
    if pattern is None:
        fig = go.Figure()
        fig.update_layout(title=f"Unknown pattern: {pattern_id}", height=400)
        return fig

    entry = meta.get(pattern_id, {})
    candles, highlight_bars = build_pattern_example(pattern_id)
    return build_pattern_definition_chart(
        pattern_id=pattern_id,
        pattern_name=pattern.name,
        signal=entry.get("signal", "BOTH"),
        candles=candles,
        highlight_bars=highlight_bars,
    )
