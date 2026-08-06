"""Applicable tax/charge rates — fetch, persist, and daily refresh scheduling."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import applicable_rates as rates_mod
from app.services.applicable_rates import (
    ApplicableRates,
    _parse_stcg_rate,
    _parse_stt_delivery_rate,
    get_applicable_rates,
    load_persisted_rates,
    refresh_applicable_rates,
    reset_applicable_rates_cache,
    save_persisted_rates,
)
from app.services.market_calendar import IST

IST_9AM = datetime(2026, 7, 30, 9, 0, tzinfo=IST)
IST_7AM = datetime(2026, 7, 30, 7, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def _reset_rates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_applicable_rates_cache()
    monkeypatch.setattr(rates_mod, "RATES_PATH", tmp_path / "applicable_rates.json")


@pytest.mark.quick
def test_parse_stt_delivery_rate_from_html() -> None:
    html = """
    <p>For equity delivery, STT is charged at 0.1% on both buy and sell side.</p>
    """
    assert _parse_stt_delivery_rate(html) == 0.001


@pytest.mark.quick
def test_parse_stcg_rate_from_html() -> None:
    html = """
    <p>Under Section 111A, short-term capital gains on listed equity are taxed at 20%.</p>
    """
    assert _parse_stcg_rate(html) == 0.20


@pytest.mark.quick
def test_refresh_applicable_rates_persists_and_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch(url: str, *, timeout: float = 12.0) -> str | None:
        if "zerodha" in url:
            return "equity delivery STT 0.1% on both buy and sell"
        if "cleartax" in url:
            return "Section 111A short-term capital gain 20%"
        return "Section 111A STCG 20%"

    monkeypatch.setattr(rates_mod, "_fetch_html", fake_fetch)

    refreshed = refresh_applicable_rates(now=IST_9AM)
    assert refreshed.stcg_tax_rate == 0.20
    assert refreshed.stt_rate == 0.001
    assert refreshed.last_refreshed_date == date(2026, 7, 30)

    reset_applicable_rates_cache()
    loaded = load_persisted_rates()
    assert loaded is not None
    assert loaded.stcg_tax_rate == 0.20
    assert loaded.stt_rate == 0.001


@pytest.mark.quick
def test_get_applicable_rates_uses_persisted_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_persisted_rates(
        ApplicableRates(
            stcg_tax_rate=0.18,
            stt_rate=0.0012,
            last_refreshed_date=date(2026, 7, 29),
            last_refreshed_at=IST_9AM,
            sources=["test"],
        )
    )
    reset_applicable_rates_cache()
    rates = get_applicable_rates()
    assert rates.stcg_tax_rate == 0.18
    assert rates.stt_rate == 0.0012


@pytest.mark.quick
def test_get_applicable_rates_reloads_unhealthy_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.services import applicable_rates as rates_mod

    stale = SimpleNamespace(
        stcg_tax_rate=0.2,
        stt_rate=0.001,
        stamp_duty_rate=0.00015,
        brokerage_rate=0.003,
        conservative_exit_ratio=0.5,
    )
    rates_mod._active = stale
    rates = get_applicable_rates()
    assert hasattr(rates, "brokerage_min_per_share_inr")
    assert hasattr(rates, "exchange_txn_rate")


@pytest.mark.quick
def test_due_rates_refresh_first_start_before_9am(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui import scheduled_rates_refresh as sched

    session: dict = {}
    monkeypatch.setattr(sched.st, "session_state", session, raising=False)

    assert sched.due_rates_refresh(now=IST_7AM) is True


@pytest.mark.quick
def test_due_rates_refresh_skips_when_already_refreshed_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ui import scheduled_rates_refresh as sched

    save_persisted_rates(
        ApplicableRates(
            last_refreshed_date=date(2026, 7, 30),
            last_refreshed_at=IST_9AM,
        )
    )
    reset_applicable_rates_cache()

    session: dict = {}
    monkeypatch.setattr(sched.st, "session_state", session, raising=False)

    assert sched.due_rates_refresh(now=IST_9AM) is False


@pytest.mark.quick
def test_due_rates_refresh_long_running_app_waits_until_9am(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ui import scheduled_rates_refresh as sched

    save_persisted_rates(
        ApplicableRates(
            last_refreshed_date=date(2026, 7, 29),
            last_refreshed_at=datetime(2026, 7, 29, 9, 0, tzinfo=IST),
        )
    )
    reset_applicable_rates_cache()

    session = {sched._SESSION_DAY_KEY: "2026-07-30"}
    monkeypatch.setattr(sched.st, "session_state", session, raising=False)

    assert sched.due_rates_refresh(now=IST_7AM) is False
    assert sched.due_rates_refresh(now=IST_9AM) is True
