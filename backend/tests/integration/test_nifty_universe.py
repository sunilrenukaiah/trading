"""NIFTY universe configuration contracts."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.nifty_universe import (
    DEFAULT_UNIVERSE,
    ensure_universe_symbols_fresh,
    get_universe_config,
    get_universe_symbols,
    is_universe_cache_fresh,
    list_universe_options,
    refresh_universe_symbols,
)


@pytest.mark.quick
def test_universe_options_include_default() -> None:
    options = list_universe_options()
    assert DEFAULT_UNIVERSE in options
    assert len(options) >= 4


@pytest.mark.quick
@pytest.mark.parametrize("universe", ["NIFTY20", "NIFTY50", "NIFTY100", "NIFTY250"])
def test_universe_configs_have_symbols(universe: str) -> None:
    cfg = get_universe_config(universe)
    assert cfg["stock_count"] > 0
    assert len(cfg["symbols"]) == cfg["stock_count"]
    assert cfg["eval_days"] > 0
    assert cfg["lookback_days"] > 0


@pytest.mark.quick
def test_ensure_universe_symbols_fresh_uses_cache_when_dated_today(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import nifty_universe as nu

    cache_file = tmp_path / "nifty_universe_cache.json"
    monkeypatch.setattr(nu, "CACHE_PATH", cache_file)
    monkeypatch.setattr(nu, "_today_ist", lambda: date(2026, 8, 5))
    nu.get_universe_symbols.cache_clear()

    cache_file.write_text(
        '{"_refreshed_at": "2026-08-05", "NIFTY250": ["AAA", "BBB"]}'
    )

    fetch_calls: list[str] = []
    monkeypatch.setattr(
        nu,
        "_fetch_nse_symbols",
        lambda name: fetch_calls.append(name) or ["SHOULD", "NOT", "FETCH"],
    )

    symbols = nu.ensure_universe_symbols_fresh("NIFTY250")

    assert symbols == ["AAA", "BBB"]
    assert fetch_calls == []
    assert nu.is_universe_cache_fresh() is True


@pytest.mark.quick
def test_ensure_universe_symbols_fresh_refreshes_when_stale(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import nifty_universe as nu

    cache_file = tmp_path / "nifty_universe_cache.json"
    monkeypatch.setattr(nu, "CACHE_PATH", cache_file)
    monkeypatch.setattr(nu, "_today_ist", lambda: date(2026, 8, 5))
    nu.get_universe_symbols.cache_clear()

    cache_file.write_text('{"NIFTY250": ["OLD", "LIST"]}')
    monkeypatch.setattr(nu, "_fetch_nse_symbols", lambda _name: ["NEW", "LIST"])

    symbols = nu.ensure_universe_symbols_fresh("NIFTY250")

    assert symbols == ["NEW", "LIST"]
    payload = cache_file.read_text()
    assert "_refreshed_at" in payload
    assert nu.is_universe_cache_fresh() is True


@pytest.mark.quick
def test_refresh_universe_symbols_writes_refreshed_at(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import nifty_universe as nu

    cache_file = tmp_path / "nifty_universe_cache.json"
    monkeypatch.setattr(nu, "CACHE_PATH", cache_file)
    monkeypatch.setattr(nu, "_today_ist", lambda: date(2026, 8, 5))
    monkeypatch.setattr(nu, "_fetch_nse_symbols", lambda _name: ["AAA", "BBB"])
    nu.get_universe_symbols.cache_clear()

    symbols = nu.refresh_universe_symbols("NIFTY250")

    assert symbols == ["AAA", "BBB"]
    assert nu.get_universe_symbols("NIFTY250") == ("AAA", "BBB")
    assert '"_refreshed_at": "2026-08-05"' in cache_file.read_text()
