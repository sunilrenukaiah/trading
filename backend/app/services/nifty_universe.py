"""NIFTY index universes for backtest and recommendation simulations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_PATH = DATA_DIR / "nifty_universe_cache.json"
NIFTY50_PATH = DATA_DIR / "nifty50.json"
_CACHE_DATE_KEY = "_refreshed_at"

UNIVERSE_LABELS: dict[str, str] = {
    "NIFTY20": "NIFTY 20 (top large caps)",
    "NIFTY50": "NIFTY 50",
    "NIFTY100": "NIFTY 100",
    "NIFTY250": "NIFTY LargeMidcap 250",
}

DEFAULT_UNIVERSE = "NIFTY250"

# Top 20 NIFTY50 names by typical index weight (liquid large caps)
NIFTY20_SYMBOLS: list[str] = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "BHARTIARTL",
    "ITC",
    "SBIN",
    "HINDUNILVR",
    "KOTAKBANK",
    "LT",
    "AXISBANK",
    "BAJFINANCE",
    "MARUTI",
    "SUNPHARMA",
    "TITAN",
    "ULTRACEMCO",
    "HCLTECH",
    "ASIANPAINT",
    "M&M",
]

_NSE_INDEX_MAP: dict[str, str] = {
    "NIFTY50": "Nifty 50",
    "NIFTY100": "Nifty 100",
    "NIFTY250": "Nifty LargeMidcap 250",
}


def list_universe_options() -> list[str]:
    return list(UNIVERSE_LABELS.keys())


def _load_nifty50_file() -> list[str]:
    data = json.loads(NIFTY50_PATH.read_text())
    return [row["symbol"] for row in data["constituents"]]


def _fetch_nse_symbols(index_name: str) -> list[str]:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    from nsefeed.indices import constituent_stock_list

    def _call() -> list[str]:
        df = constituent_stock_list("BroadMarketIndices", index_name)
        if "Symbol" not in df.columns:
            raise ValueError(f"No Symbol column for {index_name}")
        return [str(s).strip().upper() for s in df["Symbol"].tolist() if s]

    # Streamlit Cloud / non-India IPs often hang talking to NSE.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_call)
        try:
            return future.result(timeout=20)
        except FuturesTimeout as exc:
            raise TimeoutError(f"NSE constituent fetch timed out for {index_name}") from exc


@dataclass(frozen=True)
class _UniverseCacheFile:
    refreshed_at: date | None
    universes: dict[str, list[str]]


def _parse_cache_payload(raw: dict) -> _UniverseCacheFile:
    refreshed_at: date | None = None
    refreshed_raw = raw.get(_CACHE_DATE_KEY)
    if refreshed_raw:
        try:
            refreshed_at = date.fromisoformat(str(refreshed_raw))
        except ValueError:
            refreshed_at = None

    universes: dict[str, list[str]] = {}
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, list):
            continue
        universes[key.upper()] = [str(s).strip().upper() for s in value if s]
    return _UniverseCacheFile(refreshed_at=refreshed_at, universes=universes)


def _read_cache_file() -> _UniverseCacheFile:
    if not CACHE_PATH.exists():
        return _UniverseCacheFile(refreshed_at=None, universes={})
    try:
        raw = json.loads(CACHE_PATH.read_text())
        if not isinstance(raw, dict):
            return _UniverseCacheFile(refreshed_at=None, universes={})
        return _parse_cache_payload(raw)
    except (json.JSONDecodeError, OSError):
        return _UniverseCacheFile(refreshed_at=None, universes={})


def _today_ist() -> date:
    return datetime.now(IST).date()


def _write_cache_file(universes: dict[str, list[str]], *, refreshed_at: date | None = None) -> None:
    payload = {_CACHE_DATE_KEY: (refreshed_at or _today_ist()).isoformat()}
    for key, symbols in universes.items():
        payload[key.upper()] = symbols
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2))


def universe_cache_refreshed_at() -> date | None:
    """IST date when the on-disk constituent cache was last fetched from NSE."""
    return _read_cache_file().refreshed_at


def is_universe_cache_fresh(*, as_of: date | None = None) -> bool:
    """True when the cache was refreshed on or after the given IST calendar day."""
    refreshed = universe_cache_refreshed_at()
    if refreshed is None:
        return False
    return refreshed >= (as_of or _today_ist())


def _resolve_from_nse(universe: str) -> list[str]:
    cached = _read_cache_file()
    if universe in cached.universes and cached.universes[universe]:
        return cached.universes[universe]

    index_name = _NSE_INDEX_MAP.get(universe)
    if not index_name:
        raise ValueError(f"Unknown universe: {universe}")

    symbols = _fetch_nse_symbols(index_name)
    universes = dict(cached.universes)
    universes[universe] = symbols
    _write_cache_file(universes, refreshed_at=_today_ist())
    return symbols


@lru_cache(maxsize=8)
def get_universe_symbols(universe: str = DEFAULT_UNIVERSE) -> tuple[str, ...]:
    """Return symbol list for the selected NIFTY universe."""
    key = universe.upper()
    if key not in UNIVERSE_LABELS:
        key = DEFAULT_UNIVERSE

    if key == "NIFTY20":
        symbols = NIFTY20_SYMBOLS
    elif key == "NIFTY50":
        try:
            symbols = _resolve_from_nse("NIFTY50")
        except Exception:
            symbols = _load_nifty50_file()
    else:
        try:
            symbols = _resolve_from_nse(key)
        except Exception:
            # Cloud / blocked NSE: fall back to file + any on-disk cache.
            cached = _read_cache_file()
            symbols = cached.universes.get(key) or _load_nifty50_file()

    return tuple(s.upper() for s in symbols if s)


def ensure_universe_symbols_fresh(universe: str = DEFAULT_UNIVERSE) -> list[str]:
    """
    Return the constituent list, fetching from NSE only when the on-disk cache
    is missing or older than today (IST).
    """
    key = universe.upper()
    if key not in UNIVERSE_LABELS:
        key = DEFAULT_UNIVERSE

    if key == "NIFTY20":
        return list(NIFTY20_SYMBOLS)

    cached = _read_cache_file()
    if is_universe_cache_fresh() and cached.universes.get(key):
        return list(cached.universes[key])

    try:
        return refresh_universe_symbols(key)
    except Exception:
        if cached.universes.get(key):
            return list(cached.universes[key])
        return list(get_universe_symbols(key))


def refresh_universe_symbols(universe: str = DEFAULT_UNIVERSE) -> list[str]:
    """Fetch the latest index constituents from NSE and refresh the on-disk cache."""
    key = universe.upper()
    if key not in UNIVERSE_LABELS:
        key = DEFAULT_UNIVERSE

    if key == "NIFTY20":
        symbols = list(NIFTY20_SYMBOLS)
    elif key == "NIFTY50":
        try:
            symbols = _fetch_nse_symbols("Nifty 50")
        except Exception:
            symbols = _load_nifty50_file()
    else:
        index_name = _NSE_INDEX_MAP.get(key)
        if not index_name:
            raise ValueError(f"Unknown universe: {key}")
        symbols = _fetch_nse_symbols(index_name)

    symbols = [s.upper() for s in symbols if s]
    cached = _read_cache_file()
    universes = dict(cached.universes)
    universes[key] = symbols
    _write_cache_file(universes, refreshed_at=_today_ist())
    get_universe_symbols.cache_clear()
    return symbols


def get_universe_config(universe: str = DEFAULT_UNIVERSE) -> dict:
    """Settings bundle for BacktestEngine (lookback/eval from backtest_universe.json)."""
    base_path = DATA_DIR / "backtest_universe.json"
    base = json.loads(base_path.read_text()) if base_path.exists() else {}
    symbols = list(get_universe_symbols(universe))
    return {
        "universe": universe.upper(),
        "symbols": symbols,
        "lookback_days": base.get("lookback_days", 20),
        "eval_days": base.get("eval_days", 30),
        "stock_count": len(symbols),
    }


def universe_label(universe: str) -> str:
    return UNIVERSE_LABELS.get(universe.upper(), universe)
