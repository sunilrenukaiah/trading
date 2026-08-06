"""Instance isolation guard tests."""

from __future__ import annotations

import pytest

from ui.instance_guard import assert_instance_isolation, instance_label


@pytest.mark.quick
def test_main_instance_label_without_lab_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAB_MODE", raising=False)
    assert instance_label() == "main"


@pytest.mark.quick
def test_instance_isolation_passes_for_main_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAB_MODE", raising=False)
    monkeypatch.delenv("LAB_SCHEMA", raising=False)
    monkeypatch.delenv("TRADING_UI_PORT", raising=False)
    assert_instance_isolation()


@pytest.mark.quick
def test_instance_isolation_rejects_lab_mode_on_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_MODE", "1")
    monkeypatch.setenv("LAB_SCHEMA", "trading_lab")
    with pytest.raises(RuntimeError, match="main instance"):
        assert_instance_isolation()
