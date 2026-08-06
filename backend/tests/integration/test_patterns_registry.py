"""Pattern registry must load all expected strategies."""

from __future__ import annotations

import pytest

import app.strategies.patterns  # noqa: F401
from app.strategies.registry import get_all_patterns


@pytest.mark.quick
def test_patterns_registered() -> None:
    patterns = get_all_patterns()
    assert len(patterns) >= 50
    ids = {p.id for p in patterns}
    assert "p10_doji" in ids
    assert "cs_morning_star" in ids
    assert "cs_evening_star" in ids
    assert "cs_dark_cloud_cover" in ids
