"""Tests for background live-quote cache."""

from __future__ import annotations

import threading
import time

import pytest

from ui.live_quote_poller import (
    LIVE_POLL_INTERVAL_SEC,
    maybe_start_background_refresh,
    publish_refresh_result,
    reset_live_quote_cache,
    sync_cache_to_session,
)


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    reset_live_quote_cache()
    yield
    reset_live_quote_cache()


@pytest.mark.quick
def test_publish_and_sync_updates_session() -> None:
    session: dict = {}
    publish_refresh_result(
        {
            "RELIANCE": {
                "ltp": 2500.5,
                "open": 2480.0,
                "prev_close": 2475.0,
                "high": 2510.0,
            }
        },
        {"entries_filled": 1},
        symbols=["RELIANCE"],
    )
    sync_cache_to_session(session)

    assert session["position_live_quotes"]["RELIANCE"]["ltp"] == 2500.5
    assert session["position_live_quotes"]["RELIANCE"]["high"] == 2510.0
    assert session["position_live_quotes_at"] is not None
    assert "trade_plan_live_notice" in session


@pytest.mark.quick
def test_publish_accepts_legacy_float_ltp() -> None:
    session: dict = {}
    publish_refresh_result({"TATASTEEL": 120.5}, {}, symbols=["TATASTEEL"])
    sync_cache_to_session(session)
    assert session["position_live_quotes"]["TATASTEEL"]["ltp"] == 120.5
    assert session["position_live_quotes"]["TATASTEEL"]["open"] is None


@pytest.mark.quick
def test_background_refresh_runs_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    finished = threading.Event()

    def _fake_worker(symbols: list[str]) -> None:
        started.set()
        time.sleep(0.05)
        publish_refresh_result({"TCS": 3500.0}, {}, symbols=symbols)
        finished.set()

    monkeypatch.setattr("ui.live_quote_poller._fetch_worker", _fake_worker)

    maybe_start_background_refresh(["TCS"], force=True)
    assert started.wait(timeout=1.0)

    session: dict = {}
    assert not finished.is_set()
    sync_cache_to_session(session)
    assert session.get("position_live_quotes") is None

    assert finished.wait(timeout=1.0)
    sync_cache_to_session(session)
    assert session["position_live_quotes"]["TCS"]["ltp"] == 3500.0


@pytest.mark.quick
def test_interval_guards_duplicate_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_worker(symbols: list[str]) -> None:
        calls.append(symbols)
        publish_refresh_result({"INFY": 1500.0}, {}, symbols=symbols)

    monkeypatch.setattr("ui.live_quote_poller._fetch_worker", _fake_worker)

    maybe_start_background_refresh(["INFY"], force=True)
    time.sleep(0.1)
    maybe_start_background_refresh(["INFY"])
    time.sleep(0.1)

    assert len(calls) == 1


@pytest.mark.quick
def test_poll_interval_is_ten_seconds() -> None:
    assert LIVE_POLL_INTERVAL_SEC == 10
