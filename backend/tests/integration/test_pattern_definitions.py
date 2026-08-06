"""Pattern definitions catalog and example charts."""

from __future__ import annotations

import pytest

import app.strategies.patterns  # noqa: F401
from app.services.pattern_definitions import (
    build_pattern_example_chart,
    list_pattern_definitions,
    pattern_categories,
)
from app.strategies.registry import get_all_patterns


@pytest.mark.quick
def test_pattern_definitions_cover_registry() -> None:
    definitions = list_pattern_definitions()
    registry_ids = {p.id for p in get_all_patterns()}
    catalog_ids = {row.pattern_id for row in definitions}
    assert catalog_ids == registry_ids
    assert len(definitions) >= 50


@pytest.mark.quick
def test_pattern_categories_non_empty() -> None:
    categories = pattern_categories()
    assert "Candlestick" in categories
    assert len(categories) >= 4


@pytest.mark.quick
def test_pattern_example_chart_builds() -> None:
    fig = build_pattern_example_chart("cs_morning_star")
    assert fig.layout.title.text
    assert len(fig.data) >= 1
