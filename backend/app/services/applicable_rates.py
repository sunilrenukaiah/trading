"""Fetch, persist, and serve applicable Indian equity tax/charge rates."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import settings
from app.defaults import (
    DEFAULT_BROKERAGE_MIN_PER_SHARE_INR,
    DEFAULT_BROKERAGE_RATE,
    DEFAULT_BROKER_PROFILE,
    DEFAULT_CONSERVATIVE_EXIT_RATIO,
    DEFAULT_EXCHANGE_TXN_RATE,
    DEFAULT_GST_RATE,
    DEFAULT_SEBI_TURNOVER_RATE,
    DEFAULT_STAMP_DUTY_RATE,
    DEFAULT_STCG_TAX_RATE,
    DEFAULT_STT_RATE,
)

IST = ZoneInfo("Asia/Kolkata")
RATES_PATH = Path(__file__).resolve().parent.parent / "data" / "applicable_rates.json"

# Public reference pages (HTML) — statutory rates only; brokerage stays configurable.
RATE_SOURCES: tuple[dict[str, str], ...] = (
    {
        "id": "zerodha_stt",
        "url": (
            "https://support.zerodha.com/category/account-opening/resident-individual/"
            "ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated"
        ),
        "kind": "stt",
    },
    {
        "id": "cleartax_stcg",
        "url": "https://cleartax.in/s/short-term-capital-gain-on-shares",
        "kind": "stcg",
    },
    {
        "id": "bajaj_stcg",
        "url": "https://www.bajajfinserv.in/investments/understanding-short-term-capital-gains-tax",
        "kind": "stcg",
    },
)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


@dataclass
class ApplicableRates:
    stcg_tax_rate: float = DEFAULT_STCG_TAX_RATE
    stt_rate: float = DEFAULT_STT_RATE
    stamp_duty_rate: float = DEFAULT_STAMP_DUTY_RATE
    brokerage_rate: float = DEFAULT_BROKERAGE_RATE
    brokerage_min_per_share_inr: float = DEFAULT_BROKERAGE_MIN_PER_SHARE_INR
    exchange_txn_rate: float = DEFAULT_EXCHANGE_TXN_RATE
    sebi_turnover_rate: float = DEFAULT_SEBI_TURNOVER_RATE
    gst_rate: float = DEFAULT_GST_RATE
    broker_profile: str = DEFAULT_BROKER_PROFILE
    conservative_exit_ratio: float = DEFAULT_CONSERVATIVE_EXIT_RATIO
    last_refreshed_date: date | None = None
    last_refreshed_at: datetime | None = None
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> ApplicableRates:
        return cls(
            stcg_tax_rate=settings.stcg_tax_rate,
            stt_rate=settings.stt_rate,
            stamp_duty_rate=settings.stamp_duty_rate,
            brokerage_rate=settings.brokerage_rate,
            brokerage_min_per_share_inr=DEFAULT_BROKERAGE_MIN_PER_SHARE_INR,
            exchange_txn_rate=DEFAULT_EXCHANGE_TXN_RATE,
            sebi_turnover_rate=DEFAULT_SEBI_TURNOVER_RATE,
            gst_rate=DEFAULT_GST_RATE,
            broker_profile=DEFAULT_BROKER_PROFILE,
            conservative_exit_ratio=settings.conservative_exit_ratio,
        )


_REQUIRED_RATE_FIELDS: tuple[str, ...] = (
    "stcg_tax_rate",
    "stt_rate",
    "stamp_duty_rate",
    "brokerage_rate",
    "brokerage_min_per_share_inr",
    "exchange_txn_rate",
    "sebi_turnover_rate",
    "gst_rate",
    "broker_profile",
    "conservative_exit_ratio",
)


def _rates_instance_healthy(rates: ApplicableRates | None) -> bool:
    if rates is None:
        return False
    return all(hasattr(rates, name) for name in _REQUIRED_RATE_FIELDS)


_active: ApplicableRates | None = None


def _pct_to_decimal(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    return round(float(match.group(1)) / 100.0, 6)


def _parse_stt_delivery_rate(html: str) -> float | None:
    """Delivery equity STT is 0.1% on each leg → 0.001 decimal rate on transaction value."""
    window = html.lower()
    for needle in ("equity delivery", "delivery equity", "delivery-based"):
        idx = window.find(needle)
        if idx == -1:
            continue
        snippet = window[max(0, idx - 120) : idx + 400]
        rate = _pct_to_decimal(snippet)
        if rate is not None and 0.0005 <= rate <= 0.002:
            return rate
    # Fallback: common table row pattern
    match = re.search(
        r"delivery[^%]{0,120}?(\d+(?:\.\d+)?)\s*%\s*(?:\([^)]*\)\s*)?(?:on\s+both|both\s+buy)",
        window,
        re.I | re.S,
    )
    if match:
        rate = float(match.group(1)) / 100.0
        if 0.0005 <= rate <= 0.002:
            return rate
    return None


def _parse_stcg_rate(html: str) -> float | None:
    window = html.lower()
    for needle in ("section 111a", "short-term capital gain", "stcg"):
        idx = window.find(needle)
        if idx == -1:
            continue
        snippet = window[idx : idx + 500]
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%", snippet):
            rate = float(match.group(1)) / 100.0
            if 0.10 <= rate <= 0.30:
                return rate
    return None


def _fetch_html(url: str, *, timeout: float = 12.0) -> str | None:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception:
        return None


def _fetch_rates_from_web() -> tuple[dict[str, float], list[str], list[str]]:
    """Return partial rate updates, source ids, and notes."""
    updates: dict[str, float] = {}
    sources: list[str] = []
    notes: list[str] = []

    for src in RATE_SOURCES:
        html = _fetch_html(src["url"])
        if not html:
            notes.append(f"Could not fetch {src['id']}")
            continue
        sources.append(src["id"])
        if src["kind"] == "stt":
            stt = _parse_stt_delivery_rate(html)
            if stt is not None:
                updates["stt_rate"] = stt
            else:
                notes.append(f"No STT delivery rate parsed from {src['id']}")
        elif src["kind"] == "stcg":
            stcg = _parse_stcg_rate(html)
            if stcg is not None:
                updates["stcg_tax_rate"] = stcg
            else:
                notes.append(f"No STCG rate parsed from {src['id']}")

    # Stamp duty on equity delivery (Maharashtra/NSE default, statutory).
    updates.setdefault("stamp_duty_rate", DEFAULT_STAMP_DUTY_RATE)
    if "stamp_duty_rate" in updates:
        sources.append("stamp_duty_statutory_default")

    return updates, sources, notes


def _serialize(data: ApplicableRates) -> dict:
    payload = asdict(data)
    if data.last_refreshed_date:
        payload["last_refreshed_date"] = data.last_refreshed_date.isoformat()
    if data.last_refreshed_at:
        payload["last_refreshed_at"] = data.last_refreshed_at.isoformat()
    return payload


def _deserialize(payload: dict) -> ApplicableRates:
    refreshed_date = payload.get("last_refreshed_date")
    refreshed_at = payload.get("last_refreshed_at")
    return ApplicableRates(
        stcg_tax_rate=float(payload.get("stcg_tax_rate", DEFAULT_STCG_TAX_RATE)),
        stt_rate=float(payload.get("stt_rate", DEFAULT_STT_RATE)),
        stamp_duty_rate=float(payload.get("stamp_duty_rate", DEFAULT_STAMP_DUTY_RATE)),
        brokerage_rate=float(payload.get("brokerage_rate", DEFAULT_BROKERAGE_RATE)),
        brokerage_min_per_share_inr=float(
            payload.get("brokerage_min_per_share_inr", DEFAULT_BROKERAGE_MIN_PER_SHARE_INR)
        ),
        exchange_txn_rate=float(
            payload.get("exchange_txn_rate", DEFAULT_EXCHANGE_TXN_RATE)
        ),
        sebi_turnover_rate=float(
            payload.get("sebi_turnover_rate", DEFAULT_SEBI_TURNOVER_RATE)
        ),
        gst_rate=float(payload.get("gst_rate", DEFAULT_GST_RATE)),
        broker_profile=str(payload.get("broker_profile", DEFAULT_BROKER_PROFILE)),
        conservative_exit_ratio=float(
            payload.get("conservative_exit_ratio", DEFAULT_CONSERVATIVE_EXIT_RATIO)
        ),
        last_refreshed_date=(
            date.fromisoformat(refreshed_date) if refreshed_date else None
        ),
        last_refreshed_at=(
            datetime.fromisoformat(refreshed_at) if refreshed_at else None
        ),
        sources=list(payload.get("sources", [])),
        notes=list(payload.get("notes", [])),
    )


def load_persisted_rates() -> ApplicableRates | None:
    if not RATES_PATH.exists():
        return None
    try:
        payload = json.loads(RATES_PATH.read_text(encoding="utf-8"))
        return _deserialize(payload)
    except Exception:
        return None


def save_persisted_rates(rates: ApplicableRates) -> None:
    RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    RATES_PATH.write_text(
        json.dumps(_serialize(rates), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_applicable_rates() -> ApplicableRates:
    global _active
    if _active is not None and not _rates_instance_healthy(_active):
        _active = None
    if _active is None:
        persisted = load_persisted_rates()
        candidate = persisted if persisted is not None else ApplicableRates.from_settings()
        if not _rates_instance_healthy(candidate):
            candidate = ApplicableRates.from_settings()
        _active = candidate
    return _active


def reset_applicable_rates_cache() -> None:
    global _active
    _active = None


def refresh_due(*, now: datetime | None = None) -> bool:
    """True when rates have not been refreshed yet today (IST)."""
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    rates = get_applicable_rates()
    if rates.last_refreshed_date is None:
        return True
    return rates.last_refreshed_date < current.date()


def refresh_applicable_rates(*, now: datetime | None = None) -> ApplicableRates:
    """
    Fetch latest statutory rates from the web, merge with config defaults, persist.
    """
    global _active
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)

    base = ApplicableRates.from_settings()
    persisted = load_persisted_rates()
    if persisted is not None:
        base.brokerage_rate = persisted.brokerage_rate
        base.brokerage_min_per_share_inr = persisted.brokerage_min_per_share_inr
        base.exchange_txn_rate = persisted.exchange_txn_rate
        base.sebi_turnover_rate = persisted.sebi_turnover_rate
        base.gst_rate = persisted.gst_rate
        base.broker_profile = persisted.broker_profile
        base.conservative_exit_ratio = persisted.conservative_exit_ratio

    updates, sources, notes = _fetch_rates_from_web()
    merged = ApplicableRates(
        stcg_tax_rate=updates.get("stcg_tax_rate", base.stcg_tax_rate),
        stt_rate=updates.get("stt_rate", base.stt_rate),
        stamp_duty_rate=updates.get("stamp_duty_rate", base.stamp_duty_rate),
        brokerage_rate=base.brokerage_rate,
        brokerage_min_per_share_inr=base.brokerage_min_per_share_inr,
        exchange_txn_rate=base.exchange_txn_rate,
        sebi_turnover_rate=base.sebi_turnover_rate,
        gst_rate=base.gst_rate,
        broker_profile=base.broker_profile,
        conservative_exit_ratio=base.conservative_exit_ratio,
        last_refreshed_date=current.date(),
        last_refreshed_at=current,
        sources=sources,
        notes=notes,
    )

    save_persisted_rates(merged)
    _active = merged
    return merged
