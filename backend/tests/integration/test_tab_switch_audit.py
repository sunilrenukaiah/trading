"""Tab switch and page render audit timing."""

from __future__ import annotations

import time

import pytest

from ui.tab_switch_audit import audit_page_render, page_slug


@pytest.mark.quick
def test_page_slug_mapping() -> None:
    assert page_slug("Trading") == "trading"
    assert page_slug("Analysis & EOD") == "analysis_eod"
    assert page_slug("Mid day recommendation analysis") == "midday_recommendations"
    assert page_slug("Unknown Page") == "unknown_page"


@pytest.mark.quick
def test_audit_page_render_records_page_render(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict] = []

    def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("app.services.audit.record_audit_sync", fake_record)

    with audit_page_render(
        "Recommendations",
        from_page="Recommendations",
        db_ready=True,
        main_start=time.perf_counter(),
    ):
        time.sleep(0.01)

    assert len(recorded) == 1
    assert recorded[0]["action"] == "ui.page_render"
    assert recorded[0]["context"]["page_slug"] == "recommendations"
    assert recorded[0]["duration_ms"] >= 5


@pytest.mark.quick
def test_audit_page_render_records_tab_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[dict] = []

    def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("app.services.audit.record_audit_sync", fake_record)

    with audit_page_render(
        "Recommendations",
        from_page="Trading",
        db_ready=True,
        main_start=time.perf_counter() - 0.05,
    ):
        time.sleep(0.01)

    assert len(recorded) == 2
    actions = [r["action"] for r in recorded]
    assert "ui.page_render" in actions
    assert "ui.tab_switch" in actions

    switch = next(r for r in recorded if r["action"] == "ui.tab_switch")
    assert switch["context"]["from_page"] == "Trading"
    assert switch["context"]["page"] == "Recommendations"
    assert "body_ms" in switch["context"]
    assert "total_ms" in switch["context"]


@pytest.mark.quick
def test_audit_page_render_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.audit_types import AuditStatus

    recorded: list[dict] = []

    def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr("app.services.audit.record_audit_sync", fake_record)

    with pytest.raises(RuntimeError, match="boom"):
        with audit_page_render(
            "Trading",
            from_page=None,
            db_ready=False,
            main_start=time.perf_counter(),
        ):
            raise RuntimeError("boom")

    assert recorded[0]["status"] == AuditStatus.FAILED
    assert recorded[0]["error"] is not None
