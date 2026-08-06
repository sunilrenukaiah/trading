"""Tomorrow's stock recommendations driven by recent pattern performance."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select

import app.strategies.patterns  # noqa: F401
from app.defaults import (
    DEFAULT_MAX_TARGET_PROFIT_PCT,
    DEFAULT_MIN_EXPECTED_MOVE_INR,
    DEFAULT_MIN_NET_PROFIT_AFTER_TAX_INR,
    DEFAULT_MIN_RELATIVE_VOLUME,
    DEFAULT_RECOMMENDATION_MAX_TARGET_PROFIT_PCT,
    DEFAULT_REFERENCE_ALLOCATION_INR,
    DEFAULT_TARGET_ATR_MULTIPLIER,
    DEFAULT_TARGET_RESISTANCE_FACTOR,
    DEFAULT_VOLUME_LOOKBACK_DAYS,
)
from app.services.backtest import BacktestEngine, PatternResult, ProgressCallback, _predicted_close
from app.services.ohlcv_utils import finite_float, sanitize_ohlcv_dataframe, valid_candle_prices
from app.services.trade_tax import compute_net_profit, compute_sell_targets
from app.strategies.base import Signal
from app.strategies.registry import get_all_patterns

UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "data" / "recommendation_universe.json"

CAP_TIERS = ("large_cap", "mid_cap", "small_cap")

# Extra ranking weight (percentage points) for high-conviction bullish setups.
DEFAULT_PATTERN_RANKING_BOOST_PCT: dict[str, float] = {
    "p9_swing_structure": 9.0,
    "pa_nr4": 7.0,
    "cs_piercing_line": 6.0,
    "cs_bullish_kicker": 6.0,
    "cs_bullish_separating_lines": 6.0,
    "p7_engulfing": 4.0,
    "pa_falling_wedge": 0.0,
}

NR4_PATTERN_ID = "pa_nr4"
DEFAULT_NR4_CONFLUENCE_CONFIDENCE_BOOST = 8.0

# Skip recommendations with implausible prices (usually bad/stale OHLCV).
MIN_BUY_PRICE_BY_TIER = {
    "large_cap": 100.0,
    "mid_cap": 30.0,
    "small_cap": 10.0,
}


@dataclass
class PatternRanking:
    pattern_id: str
    pattern_name: str
    hit_rate_pct: float
    total_correct: int
    total_signals: int
    avg_daily_score: float


@dataclass
class StockRecommendation:
    symbol: str
    cap_tier: str
    pattern_id: str
    pattern_name: str
    pattern_hit_rate_30d: float
    signal: str
    action: str
    buy_price: float
    stop_loss: float
    resistance: float
    sell_price: float
    actual_sell_price: float
    model_profit_pct: float
    actual_profit_pct: float
    risk_reward: float
    latest_close: float
    prev_close: float
    expected_move_pct: float
    confidence_score: float
    expected_move_inr: float = 0.0
    relative_volume: float | None = None
    volume_score: float = 0.0
    supporting_patterns: list[str] = field(default_factory=list)
    price_bucket: str | None = None
    nr4_confluence: bool = False


def coerce_stock_recommendation(rec: StockRecommendation | dict) -> StockRecommendation:
    """Backfill fields from older snapshots or pre-reload Streamlit instances."""
    if isinstance(rec, dict):
        data = dict(rec)
        if "expected_move_inr" not in data:
            data["expected_move_inr"] = round(
                float(data["actual_sell_price"]) - float(data["buy_price"]),
                2,
            )
        data.setdefault("relative_volume", None)
        data.setdefault("volume_score", 0.0)
        data.setdefault("supporting_patterns", [])
        data.setdefault("price_bucket", None)
        data.setdefault("nr4_confluence", False)
        return StockRecommendation(**data)

    if hasattr(rec, "expected_move_inr"):
        return rec

    return StockRecommendation(
        symbol=rec.symbol,
        cap_tier=rec.cap_tier,
        pattern_id=rec.pattern_id,
        pattern_name=rec.pattern_name,
        pattern_hit_rate_30d=rec.pattern_hit_rate_30d,
        signal=rec.signal,
        action=rec.action,
        buy_price=rec.buy_price,
        stop_loss=rec.stop_loss,
        resistance=rec.resistance,
        sell_price=rec.sell_price,
        actual_sell_price=rec.actual_sell_price,
        model_profit_pct=rec.model_profit_pct,
        actual_profit_pct=rec.actual_profit_pct,
        risk_reward=rec.risk_reward,
        latest_close=rec.latest_close,
        prev_close=rec.prev_close,
        expected_move_pct=rec.expected_move_pct,
        confidence_score=rec.confidence_score,
        expected_move_inr=round(rec.actual_sell_price - rec.buy_price, 2),
        relative_volume=getattr(rec, "relative_volume", None),
        volume_score=float(getattr(rec, "volume_score", 0.0)),
        supporting_patterns=list(rec.supporting_patterns),
        price_bucket=getattr(rec, "price_bucket", None),
        nr4_confluence=bool(getattr(rec, "nr4_confluence", False)),
    )


def normalize_recommendation_report(report: RecommendationReport) -> RecommendationReport:
    """Ensure all nested picks expose current StockRecommendation fields."""
    report.recommendations = [
        coerce_stock_recommendation(r) for r in report.recommendations
    ]
    report.price_bucket_recommendations = {
        label: [coerce_stock_recommendation(r) for r in recs]
        for label, recs in report.price_bucket_recommendations.items()
    }
    return report


@dataclass
class RecommendationReport:
    generated_at: date
    prediction_date: date
    data_through_date: date
    lookback_days: int
    eval_days: int
    top_patterns: list[PatternRanking]
    recommendations: list[StockRecommendation]
    tier_counts: dict[str, int]
    max_target_profit_pct: float = DEFAULT_MAX_TARGET_PROFIT_PCT
    notes: list[str] = field(default_factory=list)
    price_bucket_recommendations: dict[str, list[StockRecommendation]] = field(default_factory=dict)
    price_bucket_counts: dict[str, int] = field(default_factory=dict)


def all_report_recommendations(report: RecommendationReport) -> list[StockRecommendation]:
    """Cap-tier picks plus price-bucket picks (deduped by symbol, first wins)."""
    seen: set[str] = set()
    merged: list[StockRecommendation] = []
    for rec in report.recommendations:
        if rec.symbol not in seen:
            merged.append(rec)
            seen.add(rec.symbol)
    for bucket_recs in report.price_bucket_recommendations.values():
        for rec in bucket_recs:
            if rec.symbol not in seen:
                merged.append(rec)
                seen.add(rec.symbol)
    return merged


def dedupe_price_bucket_recommendations(
    bucket_recs: dict[str, list[StockRecommendation]],
) -> dict[str, list[StockRecommendation]]:
    """Each symbol appears in at most one price bucket (first bucket wins)."""
    used: set[str] = set()
    deduped: dict[str, list[StockRecommendation]] = {}
    for label, recs in bucket_recs.items():
        unique: list[StockRecommendation] = []
        for rec in recs:
            if rec.symbol in used:
                continue
            unique.append(rec)
            used.add(rec.symbol)
        deduped[label] = unique
    return deduped


def sanitize_price_bucket_recommendations(
    report: RecommendationReport,
) -> dict[str, list[StockRecommendation]]:
    """Display/engine cleanup: cap-tier symbols never appear in price buckets."""
    tier_symbols = {r.symbol for r in report.recommendations}
    used: set[str] = set(tier_symbols)
    cleaned: dict[str, list[StockRecommendation]] = {}
    for label, recs in report.price_bucket_recommendations.items():
        unique: list[StockRecommendation] = []
        for rec in recs:
            if rec.symbol in used:
                continue
            unique.append(rec)
            used.add(rec.symbol)
        cleaned[label] = unique
    return cleaned


def apply_price_bucket_sanitize(report: RecommendationReport) -> None:
    """Mutate report buckets in place (cached snapshots / legacy rows)."""
    report.price_bucket_recommendations = sanitize_price_bucket_recommendations(report)
    report.price_bucket_counts = {
        label: len(recs) for label, recs in report.price_bucket_recommendations.items()
    }


def _symbol_tier_map(universe: dict | None = None) -> dict[str, str]:
    u = universe or _load_universe()
    mapping: dict[str, str] = {}
    for tier in CAP_TIERS:
        for sym in u.get(tier, []):
            mapping[sym.upper()] = tier
    return mapping


def _load_universe() -> dict:
    return json.loads(UNIVERSE_PATH.read_text())


def pattern_ranking_boosts() -> dict[str, float]:
    """Pattern id -> extra hit-rate points used for ranking/tie-breaks only."""
    cfg = _load_universe()
    raw = cfg.get("pattern_ranking_boost_pct") or {}
    merged = dict(DEFAULT_PATTERN_RANKING_BOOST_PCT)
    merged.update({str(k): float(v) for k, v in raw.items()})
    return merged


def pattern_ranking_boost(pattern_id: str) -> float:
    return pattern_ranking_boosts().get(pattern_id, 0.0)


def pattern_exclude_ids() -> set[str]:
    cfg = _load_universe()
    raw = cfg.get("pattern_exclude_ids") or []
    return {str(pid) for pid in raw}


def pattern_max_picks_per_day() -> dict[str, int]:
    cfg = _load_universe()
    raw = cfg.get("pattern_max_picks_per_day") or {}
    return {str(k): int(v) for k, v in raw.items()}


def target_atr_multiplier() -> float:
    cfg = _load_universe()
    return float(cfg.get("target_atr_multiplier", DEFAULT_TARGET_ATR_MULTIPLIER))


def target_resistance_factor() -> float:
    cfg = _load_universe()
    return float(cfg.get("target_resistance_factor", DEFAULT_TARGET_RESISTANCE_FACTOR))


def universe_default_max_target_profit_pct() -> float:
    cfg = _load_universe()
    return float(
        cfg.get("default_max_target_profit_pct", DEFAULT_RECOMMENDATION_MAX_TARGET_PROFIT_PCT)
    )


def reference_allocation_inr() -> float:
    cfg = _load_universe()
    return float(cfg.get("reference_allocation_inr", DEFAULT_REFERENCE_ALLOCATION_INR))


def min_net_profit_after_tax_inr() -> float:
    cfg = _load_universe()
    return float(cfg.get("min_net_profit_after_tax_inr", DEFAULT_MIN_NET_PROFIT_AFTER_TAX_INR))


def net_profit_for_reference_position(buy_price: float, sell_price: float) -> float:
    """Net P&L after tax/charges for a typical tier slice (used to reject unprofitable picks)."""
    if buy_price <= 0 or sell_price <= buy_price:
        return -1.0
    shares = max(1, int(reference_allocation_inr() // buy_price))
    return compute_net_profit(shares, buy_price, sell_price).net_profit_after_tax


def passes_net_profit_gate(buy_price: float, sell_price: float) -> bool:
    return net_profit_for_reference_position(buy_price, sell_price) >= min_net_profit_after_tax_inr()


def _adjust_actual_sell_for_net_profit(
    buy_price: float,
    actual_sell: float,
    model_sell: float,
) -> float | None:
    """Raise actual sell toward model cap until reference-size net profit clears the gate."""
    if buy_price <= 0 or actual_sell <= buy_price:
        return None
    cap = max(actual_sell, model_sell)
    step = max(0.05, round(buy_price * 0.002, 2))
    sell = actual_sell
    while sell <= cap + 1e-9:
        if passes_net_profit_gate(buy_price, sell):
            return round(sell, 2)
        sell = round(sell + step, 2)
    return None


def _pattern_scan_order(
    qualified_patterns: list[PatternRanking],
    fallback_patterns: list[PatternRanking] | None,
    *,
    min_hit_rate: float,
    max_pattern_count: int | None,
) -> list[tuple[PatternRanking, float]]:
    """Patterns to try one-at-a-time: qualified first, then expanded pool at 0% floor."""
    excluded = pattern_exclude_ids()
    qualified_ids = {p.pattern_id for p in qualified_patterns}
    order: list[PatternRanking] = []
    seen: set[str] = set()

    for ranking in qualified_patterns:
        if ranking.pattern_id in excluded or ranking.pattern_id in seen:
            continue
        order.append(ranking)
        seen.add(ranking.pattern_id)

    if fallback_patterns:
        for ranking in sorted(fallback_patterns, key=_ranking_sort_key, reverse=True):
            if ranking.pattern_id in excluded or ranking.pattern_id in seen:
                continue
            order.append(ranking)
            seen.add(ranking.pattern_id)

    if max_pattern_count is not None:
        extended = order[:max_pattern_count]
    else:
        extended = order

    pairs: list[tuple[PatternRanking, float]] = []
    for ranking in extended:
        floor = min_hit_rate if ranking.pattern_id in qualified_ids else 0.0
        pairs.append((ranking, floor))

    # If still room to expand, append any remaining fallback patterns at 0% floor.
    if max_pattern_count is not None and len(order) > max_pattern_count:
        for ranking in order[max_pattern_count:]:
            pairs.append((ranking, 0.0))

    return pairs


def _filter_excluded_rankings(rankings: list[PatternRanking]) -> list[PatternRanking]:
    excluded = pattern_exclude_ids()
    if not excluded:
        return rankings
    return [r for r in rankings if r.pattern_id not in excluded]


def nr4_confluence_confidence_boost() -> float:
    cfg = _load_universe()
    return float(cfg.get("nr4_confluence_confidence_boost", DEFAULT_NR4_CONFLUENCE_CONFIDENCE_BOOST))


def always_scan_nr4() -> bool:
    cfg = _load_universe()
    return bool(cfg.get("always_scan_nr4", True))


def _expand_scan_pattern_ids(active_ids: set[str], pattern_hit: dict[str, float]) -> set[str]:
    """Widen tier scans so NR4 is evaluated alongside the active pattern set."""
    scan_ids = set(active_ids)
    if always_scan_nr4() and NR4_PATTERN_ID in pattern_hit:
        scan_ids.add(NR4_PATTERN_ID)
    return scan_ids


def _ranking_sort_key(r: PatternRanking) -> tuple[float, float, int]:
    return (
        r.hit_rate_pct + pattern_ranking_boost(r.pattern_id),
        r.avg_daily_score,
        r.total_correct,
    )


def _tier_symbols(universe: dict) -> dict[str, list[str]]:
    return {tier: universe.get(tier, []) for tier in CAP_TIERS}


def classify_symbol_tier_by_price(close: float) -> str | None:
    """Map latest close to large / mid / small cap using the same floors as pick validation."""
    if close >= MIN_BUY_PRICE_BY_TIER["large_cap"]:
        return "large_cap"
    if close >= MIN_BUY_PRICE_BY_TIER["mid_cap"]:
        return "mid_cap"
    if close >= MIN_BUY_PRICE_BY_TIER["small_cap"]:
        return "small_cap"
    return None


def symbol_tier_map_from_data(symbol_data: dict[str, pd.DataFrame]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for symbol, df in symbol_data.items():
        if df.empty:
            continue
        close = finite_float(df.iloc[-1]["close"])
        if close is None:
            continue
        tier = classify_symbol_tier_by_price(close)
        if tier:
            mapping[symbol.upper()] = tier
    return mapping


def partition_symbol_data_by_tier(
    symbol_data: dict[str, pd.DataFrame],
) -> dict[str, dict[str, pd.DataFrame]]:
    """Split NIFTY250 OHLCV into cap tiers by latest close (₹100 / ₹30 / ₹10 floors)."""
    by_tier: dict[str, dict[str, pd.DataFrame]] = {tier: {} for tier in CAP_TIERS}
    for symbol, df in symbol_data.items():
        if df.empty:
            continue
        close = finite_float(df.iloc[-1]["close"])
        if close is None:
            continue
        tier = classify_symbol_tier_by_price(close)
        if tier:
            by_tier[tier][symbol] = df
    return by_tier


def _support_level(df: pd.DataFrame, lookback: int = 10) -> float:
    window = df.tail(lookback)
    return float(window["low"].astype(float).min())


def _resistance_level(df: pd.DataFrame, lookback: int = 20) -> float:
    window = df.tail(lookback)
    return float(window["high"].astype(float).max())


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 2.0
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    closes = df["close"].astype(float)
    tr = pd.concat(
        [
            highs - lows,
            (highs - closes.shift()).abs(),
            (lows - closes.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(tr.tail(period).mean())
    last = float(closes.iloc[-1])
    return (atr / last * 100) if last else 2.0


def _volume_metrics(
    df: pd.DataFrame,
    *,
    lookback: int = DEFAULT_VOLUME_LOOKBACK_DAYS,
) -> tuple[float | None, float]:
    """Return (relative_volume vs prior N-day avg, confidence boost/penalty)."""
    if "volume" not in df.columns or len(df) < 2:
        return None, 0.0

    volumes = df["volume"].astype(float)
    latest = float(volumes.iloc[-1])
    if latest <= 0:
        return None, 0.0

    history = volumes.iloc[:-1].tail(lookback)
    if history.empty:
        return None, 0.0
    avg = float(history.mean())
    if avg <= 0:
        return None, 0.0

    relative = latest / avg
    if relative >= 2.0:
        score = 15.0
    elif relative >= 1.5:
        score = 12.0
    elif relative >= 1.2:
        score = 8.0
    elif relative >= 1.0:
        score = 4.0
    elif relative >= DEFAULT_MIN_RELATIVE_VOLUME:
        score = 0.0
    else:
        score = -8.0
    return round(relative, 2), score


def rank_patterns(
    symbol_data: dict[str, pd.DataFrame],
    eval_days: int = 15,
    lookback_days: int = 20,
    min_signals: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> list[PatternRanking]:
    """Rank patterns by BUY-signal success over the last eval_days on the given universe."""
    engine = BacktestEngine(eval_days=eval_days, lookback_days=lookback_days)
    report = engine.run_on_data(
        symbol_data,
        count_signal=Signal.BULLISH,
        progress_callback=progress_callback,
    )
    rankings: list[PatternRanking] = []

    for pr in report.patterns:
        if pr.total_signals < min_signals:
            continue
        rankings.append(
            PatternRanking(
                pattern_id=pr.pattern_id,
                pattern_name=pr.pattern_name,
                hit_rate_pct=round(pr.overall_hit_rate, 1),
                total_correct=pr.total_correct,
                total_signals=pr.total_signals,
                avg_daily_score=round(pr.avg_daily_score, 2),
            )
        )

    rankings.sort(key=_ranking_sort_key, reverse=True)
    return rankings


def _progress_phase(
    callback: ProgressCallback | None,
    start: int,
    end: int,
) -> ProgressCallback | None:
    """Map nested 0..total progress into a slice of the 0..100 bar scale."""
    if callback is None:
        return None

    def wrapped(
        current: int,
        total: int,
        message: str,
        partial: dict[str, PatternResult] | None,
    ) -> None:
        ratio = current / max(total, 1)
        synthetic = start + int(ratio * (end - start))
        callback(synthetic, 100, message, partial)

    return wrapped


def recommendation_pattern_rankings(
    symbol_data: dict[str, pd.DataFrame],
    *,
    eval_days: int | None = None,
    lookback_days: int | None = None,
    min_hit_rate: float | None = None,
    top_n: int | None = None,
    min_signals: int = 5,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[PatternRanking], list[PatternRanking]]:
    """Shared ranking used by recommendations UI and Trading tab stats."""
    cfg = _load_universe()
    eval_days = eval_days if eval_days is not None else cfg.get("eval_days", 15)
    lookback_days = lookback_days if lookback_days is not None else cfg.get("lookback_days", 20)
    min_hit_rate = min_hit_rate if min_hit_rate is not None else cfg.get("min_pattern_hit_rate_pct", 55)
    top_n = top_n if top_n is not None else cfg.get("top_patterns_count", 3)

    all_rankings = rank_patterns(
        symbol_data,
        eval_days=eval_days,
        lookback_days=lookback_days,
        min_signals=min_signals,
        progress_callback=progress_callback,
    )
    all_rankings = _filter_excluded_rankings(all_rankings)
    top_patterns = select_top_patterns(all_rankings, min_hit_rate=min_hit_rate, top_n=top_n)
    return all_rankings, top_patterns


def select_top_patterns(
    rankings: list[PatternRanking],
    *,
    min_hit_rate: float = 55.0,
    top_n: int = 3,
) -> list[PatternRanking]:
    """Top N patterns by hit rate that meet the minimum success threshold."""
    eligible = [r for r in rankings if r.hit_rate_pct >= min_hit_rate]
    return eligible[:top_n]


def qualified_pattern_rankings(
    rankings: list[PatternRanking],
    *,
    min_hit_rate: float = 55.0,
) -> list[PatternRanking]:
    """All patterns meeting the hit-rate floor, best first (for pick expansion)."""
    return [r for r in rankings if r.hit_rate_pct >= min_hit_rate]


def _tier_display(tier_key: str) -> str:
    return tier_key.replace("_", " ").title()


def _evaluate_symbol(
    symbol: str,
    df: pd.DataFrame,
    *,
    tier_key: str,
    allowed_pattern_ids: set[str],
    pattern_hit: dict[str, float],
    pattern_map: dict,
    lookback: int,
    min_hit_rate: float,
    max_target_profit_pct: float | None,
    max_buy_price: float | None = None,
    price_bucket: str | None = None,
    min_expected_move_inr: float = DEFAULT_MIN_EXPECTED_MOVE_INR,
    min_relative_volume: float = DEFAULT_MIN_RELATIVE_VOLUME,
    volume_lookback: int = DEFAULT_VOLUME_LOOKBACK_DAYS,
    progress_callback: ProgressCallback | None = None,
    progress_label: str | None = None,
) -> tuple[StockRecommendation | None, date | None]:
    if len(df) < lookback + 1:
        return None, None

    pos = len(df) - 1
    lookback_df = df.iloc[pos - lookback : pos].copy()
    prev_close = finite_float(df.iloc[pos - 1]["close"])
    latest_close = finite_float(df.iloc[pos]["close"])
    if latest_close is None or prev_close is None:
        return None, None

    through = df.iloc[pos]["trade_date"]
    if hasattr(through, "date"):
        through = through.date()

    bullish_patterns: list[tuple[str, str, float]] = []
    pattern_ids = sorted(allowed_pattern_ids)
    excluded = pattern_exclude_ids()
    atr_mult = target_atr_multiplier()
    res_factor = target_resistance_factor()
    for pi, pid in enumerate(pattern_ids, start=1):
        if pid in excluded:
            continue
        pattern = pattern_map.get(pid)
        if not pattern:
            continue
        if progress_callback and progress_label:
            progress_callback(
                pi,
                len(pattern_ids),
                f"{progress_label} · {pattern.name} ({pi}/{len(pattern_ids)})",
                None,
            )
        signal = pattern.evaluate(lookback_df)
        if signal != Signal.BULLISH:
            continue
        rate = pattern_hit.get(pid, 0)
        if rate < min_hit_rate:
            continue
        bullish_patterns.append((pid, pattern.name, rate))

    if not bullish_patterns:
        return None, through

    best_pid, best_name, best_rate = max(
        bullish_patterns,
        key=lambda x: x[2] + pattern_ranking_boost(x[0]),
    )
    supporting = [name for _, name, _ in bullish_patterns if name != best_name]

    buy_price = round(latest_close, 2)
    min_price = MIN_BUY_PRICE_BY_TIER.get(tier_key, 10.0)
    if buy_price < min_price:
        return None, through
    if max_buy_price is not None and buy_price >= max_buy_price:
        return None, through

    support = _support_level(lookback_df)
    resistance = _resistance_level(lookback_df)
    atr_pct = _atr_pct(lookback_df)

    stop_loss = round(min(support, buy_price * (1 - atr_pct / 100 * 1.5)), 2)
    raw_target = max(
        _predicted_close(Signal.BULLISH, latest_close, lookback_df),
        resistance * res_factor,
    )
    min_raw = round(buy_price * (1 + max(atr_pct * atr_mult, 0.25) / 100), 2)
    raw_target = max(raw_target, min_raw)
    targets = compute_sell_targets(buy_price, raw_target, max_profit_pct=max_target_profit_pct)
    if targets.actual_sell_price <= buy_price:
        return None, through

    expected_move_inr = round(targets.actual_sell_price - buy_price, 2)
    if expected_move_inr < min_expected_move_inr:
        return None, through

    adjusted_sell = _adjust_actual_sell_for_net_profit(
        buy_price, targets.actual_sell_price, targets.model_sell_price
    )
    if adjusted_sell is None:
        return None, through

    actual_sell = adjusted_sell
    expected_move_inr = round(actual_sell - buy_price, 2)
    actual_profit_pct = round((actual_sell - buy_price) / buy_price * 100, 2) if buy_price else 0.0

    volume_slice = df.iloc[max(0, pos - volume_lookback) : pos + 1]
    relative_volume, volume_score = _volume_metrics(volume_slice, lookback=volume_lookback)
    if relative_volume is not None and relative_volume < min_relative_volume:
        return None, through

    resistance = round(resistance, 2)

    risk = buy_price - stop_loss
    reward = actual_sell - buy_price
    risk_reward = round(reward / risk, 2) if risk > 0 else 0.0
    agreement_boost = min(len(bullish_patterns) * 5, 15)
    bullish_ids = {pid for pid, _, _ in bullish_patterns}
    nr4_confluence = NR4_PATTERN_ID in bullish_ids and len(bullish_ids) >= 2
    confluence_boost = nr4_confluence_confidence_boost() if nr4_confluence else 0.0
    confidence = round(min(best_rate + agreement_boost + volume_score + confluence_boost, 99), 1)

    rec = StockRecommendation(
        symbol=symbol,
        cap_tier=_tier_display(tier_key),
        pattern_id=best_pid,
        pattern_name=best_name,
        pattern_hit_rate_30d=best_rate,
        signal="BULLISH",
        action="BUY",
        buy_price=buy_price,
        stop_loss=stop_loss,
        resistance=resistance,
        sell_price=targets.model_sell_price,
        actual_sell_price=actual_sell,
        model_profit_pct=targets.model_profit_pct,
        actual_profit_pct=actual_profit_pct,
        risk_reward=risk_reward,
        latest_close=latest_close,
        prev_close=prev_close,
        expected_move_pct=actual_profit_pct,
        expected_move_inr=expected_move_inr,
        relative_volume=relative_volume,
        volume_score=volume_score,
        confidence_score=confidence,
        supporting_patterns=supporting[:3],
        price_bucket=price_bucket,
        nr4_confluence=nr4_confluence,
    )
    return rec, through


def _collect_recommendations(
    symbol_data: dict[str, pd.DataFrame],
    *,
    tier_key: str,
    qualified_patterns: list[PatternRanking],
    lookback: int,
    min_hit_rate: float,
    min_count: int,
    max_count: int,
    max_target_profit_pct: float | None,
    min_expected_move_inr: float = DEFAULT_MIN_EXPECTED_MOVE_INR,
    min_relative_volume: float = DEFAULT_MIN_RELATIVE_VOLUME,
    volume_lookback: int = DEFAULT_VOLUME_LOOKBACK_DAYS,
    initial_pattern_count: int = 3,
    max_pattern_count: int | None = None,
    max_buy_price: float | None = None,
    price_bucket: str | None = None,
    symbol_tier: dict[str, str] | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_offset: int = 0,
    progress_total: int = 1,
    exclude_symbols: set[str] | None = None,
    fallback_patterns: list[PatternRanking] | None = None,
    fallback_min_hit_rate: float = 45.0,
) -> list[StockRecommendation]:
    """Pick up to max_count symbols, trying one pattern at a time until min_count met."""
    if not symbol_data:
        return []
    if not qualified_patterns and not fallback_patterns:
        return []

    pattern_map = {p.id: p for p in get_all_patterns()}
    pattern_hit: dict[str, float] = {}
    for p in qualified_patterns:
        pattern_hit[p.pattern_id] = p.hit_rate_pct
    if fallback_patterns:
        for p in fallback_patterns:
            pattern_hit.setdefault(p.pattern_id, p.hit_rate_pct)

    selected: list[StockRecommendation] = []
    used_symbols: set[str] = set(exclude_symbols or ())
    pattern_pick_counts: dict[str, int] = {}
    max_picks_by_pattern = pattern_max_picks_per_day()
    symbol_items = list(symbol_data.items())
    work_step = progress_offset

    scan_plan = _pattern_scan_order(
        qualified_patterns,
        fallback_patterns,
        min_hit_rate=min_hit_rate,
        max_pattern_count=max_pattern_count,
    )

    def _scan_pattern(
        pattern_id: str,
        pattern_name: str,
        *,
        hit_rate_floor: float,
    ) -> None:
        nonlocal work_step
        if len(selected) >= max_count:
            return
        candidates: list[StockRecommendation] = []
        for idx, (symbol, df) in enumerate(symbol_items, start=1):
            if symbol in used_symbols:
                continue
            work_step += 1
            if progress_callback:
                tier_label = _tier_display((symbol_tier or {}).get(symbol, tier_key))
                progress_callback(
                    min(work_step, progress_total),
                    progress_total,
                    f"{tier_label} · {symbol} ({idx}/{len(symbol_items)}) · {pattern_name}",
                    None,
                )
            sym_tier = (symbol_tier or {}).get(symbol, tier_key)
            rec, _ = _evaluate_symbol(
                symbol,
                df,
                tier_key=sym_tier,
                allowed_pattern_ids={pattern_id},
                pattern_hit=pattern_hit,
                pattern_map=pattern_map,
                lookback=lookback,
                min_hit_rate=hit_rate_floor,
                max_target_profit_pct=max_target_profit_pct,
                max_buy_price=max_buy_price,
                price_bucket=price_bucket,
                min_expected_move_inr=min_expected_move_inr,
                min_relative_volume=min_relative_volume,
                volume_lookback=volume_lookback,
                progress_callback=progress_callback,
                progress_label=f"{_tier_display(sym_tier)} · {symbol}",
            )
            if rec is not None and rec.pattern_id == pattern_id:
                candidates.append(rec)

        candidates.sort(
            key=lambda r: (
                r.confidence_score,
                1 if r.nr4_confluence else 0,
                r.volume_score,
                r.expected_move_inr,
                r.pattern_hit_rate_30d,
            ),
            reverse=True,
        )
        for rec in candidates:
            if len(selected) >= max_count:
                break
            if rec.symbol in used_symbols:
                continue
            cap = max_picks_by_pattern.get(rec.pattern_id)
            if cap is not None and pattern_pick_counts.get(rec.pattern_id, 0) >= cap:
                continue
            selected.append(rec)
            used_symbols.add(rec.symbol)
            pattern_pick_counts[rec.pattern_id] = pattern_pick_counts.get(rec.pattern_id, 0) + 1
            if len(selected) >= min_count:
                break

    for ranking, floor in scan_plan:
        if len(selected) >= min_count:
            break
        _scan_pattern(ranking.pattern_id, ranking.pattern_name, hit_rate_floor=floor)

    return selected[:max_count]


# Backward-compatible alias
rank_patterns_30d = rank_patterns


def build_recommendations(
    symbol_data_by_tier: dict[str, dict[str, pd.DataFrame]],
    qualified_patterns: list[PatternRanking],
    *,
    min_per_tier: int = 3,
    max_per_tier: int = 3,
    min_per_price_bucket: int = 3,
    max_per_price_bucket: int = 3,
    price_buckets_inr: list[int] | None = None,
    initial_pattern_count: int = 3,
    max_patterns_for_picks: int = 20,
    min_hit_rate: float = 55.0,
    max_target_profit_pct: float | None = None,
    min_expected_move_inr: float = DEFAULT_MIN_EXPECTED_MOVE_INR,
    min_relative_volume: float = DEFAULT_MIN_RELATIVE_VOLUME,
    volume_lookback: int = DEFAULT_VOLUME_LOOKBACK_DAYS,
    progress_callback: ProgressCallback | None = None,
    all_pattern_rankings: list[PatternRanking] | None = None,
    bucket_symbol_data: dict[str, pd.DataFrame] | None = None,
) -> tuple[
    list[StockRecommendation],
    dict[str, list[StockRecommendation]],
    date | None,
    date | None,
]:
    universe = _load_universe()
    lookback = universe.get("lookback_days", 20)
    buckets = price_buckets_inr or universe.get("price_buckets_inr", [100, 500, 1000])

    recs: list[StockRecommendation] = []
    price_bucket_recs: dict[str, list[StockRecommendation]] = {}
    data_through: date | None = None

    if not qualified_patterns:
        return recs, price_bucket_recs, None, None

    symbol_tier: dict[str, str] = {}
    all_symbol_data: dict[str, pd.DataFrame] = {}
    if bucket_symbol_data:
        all_symbol_data.update(bucket_symbol_data)
    for tier, tier_data in symbol_data_by_tier.items():
        for symbol, df in tier_data.items():
            all_symbol_data[symbol] = df
            symbol_tier[symbol.upper()] = tier
            if len(df) >= lookback + 1:
                pos = len(df) - 1
                through = df.iloc[pos]["trade_date"]
                if hasattr(through, "date"):
                    through = through.date()
                data_through = through if data_through is None else min(data_through, through)

    cap_patterns = min(max_patterns_for_picks, len(qualified_patterns))
    pattern_rounds = max(1, cap_patterns - min(initial_pattern_count, cap_patterns) + 1)
    tier_symbol_count = sum(len(d) for d in symbol_data_by_tier.values())
    bucket_symbol_count = len(all_symbol_data) * len(buckets)
    progress_total = max(1, (tier_symbol_count + bucket_symbol_count) * pattern_rounds)
    progress_step = 0
    picked_symbols: set[str] = set()
    fallback_pool = all_pattern_rankings or qualified_patterns

    for tier, tier_data in symbol_data_by_tier.items():
        tier_picks = _collect_recommendations(
            tier_data,
            tier_key=tier,
            qualified_patterns=qualified_patterns,
            lookback=lookback,
            min_hit_rate=min_hit_rate,
            min_count=min_per_tier,
            max_count=max_per_tier,
            max_target_profit_pct=max_target_profit_pct,
            min_expected_move_inr=min_expected_move_inr,
            min_relative_volume=min_relative_volume,
            volume_lookback=volume_lookback,
            initial_pattern_count=initial_pattern_count,
            max_pattern_count=max_patterns_for_picks,
            progress_callback=progress_callback,
            progress_offset=progress_step,
            progress_total=progress_total,
            fallback_patterns=fallback_pool,
        )
        progress_step += len(tier_data) * pattern_rounds
        picked_symbols.update(r.symbol for r in tier_picks)
        recs.extend(tier_picks)

    bucket_used_symbols: set[str] = set(picked_symbols)
    for cap in buckets:
        label = f"Below ₹{cap:,}"
        bucket_picks = _collect_recommendations(
            all_symbol_data,
            tier_key="small_cap",
            symbol_tier=symbol_tier,
            qualified_patterns=qualified_patterns,
            lookback=lookback,
            min_hit_rate=min_hit_rate,
            min_count=min_per_price_bucket,
            max_count=max_per_price_bucket,
            max_target_profit_pct=max_target_profit_pct,
            min_expected_move_inr=min_expected_move_inr,
            min_relative_volume=min_relative_volume,
            volume_lookback=volume_lookback,
            initial_pattern_count=initial_pattern_count,
            max_pattern_count=max_patterns_for_picks,
            max_buy_price=float(cap),
            price_bucket=label,
            progress_callback=progress_callback,
            progress_offset=progress_step,
            progress_total=progress_total,
            exclude_symbols=bucket_used_symbols,
            fallback_patterns=fallback_pool,
        )
        progress_step += len(all_symbol_data) * pattern_rounds
        bucket_used_symbols.update(r.symbol for r in bucket_picks)
        price_bucket_recs[label] = bucket_picks

    prediction_date: date | None = None
    if data_through:
        from app.services.market_calendar import recommendation_prediction_date

        prediction_date = recommendation_prediction_date(data_through)

    return recs, price_bucket_recs, data_through, prediction_date


def run_recommendation_engine(
    symbol_data_by_tier: dict[str, dict[str, pd.DataFrame]],
    *,
    ranking_data_by_tier: dict[str, dict[str, pd.DataFrame]] | None = None,
    bucket_symbol_data: dict[str, pd.DataFrame] | None = None,
    max_target_profit_pct: float | None = None,
    progress_callback: ProgressCallback | None = None,
) -> RecommendationReport:
    allowed = market_universe_symbol_set()
    symbol_data_by_tier = filter_tier_symbol_data_to_market_universe(
        symbol_data_by_tier, allowed=allowed
    )
    if bucket_symbol_data is not None:
        bucket_symbol_data = filter_symbol_data_to_market_universe(
            bucket_symbol_data, allowed=allowed
        )

    universe = _load_universe()
    lookback = universe.get("lookback_days", 20)
    eval_days = universe.get("eval_days", 15)
    min_per_tier = universe.get("min_per_tier", universe.get("max_per_tier", 3))
    max_per_tier = universe.get("max_per_tier", 3)
    min_per_price_bucket = universe.get("min_per_price_bucket", 3)
    max_per_price_bucket = universe.get("max_per_price_bucket", 3)
    top_patterns_count = universe.get("top_patterns_count", 3)
    min_hit_rate = universe.get("min_pattern_hit_rate_pct", 55)
    max_patterns_for_picks = universe.get("max_patterns_for_picks", 20)
    price_buckets_inr = universe.get("price_buckets_inr", [100, 500, 1000])
    min_expected_move_inr = float(
        universe.get("min_expected_move_inr", DEFAULT_MIN_EXPECTED_MOVE_INR)
    )
    min_relative_volume = float(
        universe.get("min_relative_volume", DEFAULT_MIN_RELATIVE_VOLUME)
    )
    volume_lookback = int(universe.get("volume_lookback_days", DEFAULT_VOLUME_LOOKBACK_DAYS))
    max_target_pct = (
        max_target_profit_pct
        if max_target_profit_pct is not None
        else universe_default_max_target_profit_pct()
    )

    ranking_source = ranking_data_by_tier or symbol_data_by_tier
    all_data = {
        sym: df for tier_data in ranking_source.values() for sym, df in tier_data.items()
    }
    all_rankings, top_patterns = recommendation_pattern_rankings(
        all_data,
        eval_days=eval_days,
        lookback_days=lookback,
        min_hit_rate=min_hit_rate,
        top_n=top_patterns_count,
        progress_callback=_progress_phase(progress_callback, 12, 82),
    )
    qualified = qualified_pattern_rankings(all_rankings, min_hit_rate=min_hit_rate)

    recommendations, price_bucket_recs, data_through, prediction_date = build_recommendations(
        symbol_data_by_tier,
        qualified,
        min_per_tier=min_per_tier,
        max_per_tier=max_per_tier,
        min_per_price_bucket=min_per_price_bucket,
        max_per_price_bucket=max_per_price_bucket,
        price_buckets_inr=price_buckets_inr,
        initial_pattern_count=top_patterns_count,
        max_patterns_for_picks=max_patterns_for_picks,
        min_hit_rate=min_hit_rate,
        max_target_profit_pct=max_target_pct,
        min_expected_move_inr=min_expected_move_inr,
        min_relative_volume=min_relative_volume,
        volume_lookback=volume_lookback,
        progress_callback=_progress_phase(progress_callback, 82, 96),
        all_pattern_rankings=all_rankings,
        bucket_symbol_data=bucket_symbol_data,
    )

    tier_data_counts = {tier: len(symbol_data_by_tier.get(tier, {})) for tier in CAP_TIERS}
    tier_rec_counts = {
        "large_cap": sum(1 for r in recommendations if r.cap_tier == "Large Cap"),
        "mid_cap": sum(1 for r in recommendations if r.cap_tier == "Mid Cap"),
        "small_cap": sum(1 for r in recommendations if r.cap_tier == "Small Cap"),
    }
    price_bucket_counts = {label: len(recs) for label, recs in price_bucket_recs.items()}

    from app.services.applicable_rates import get_applicable_rates

    rates = get_applicable_rates()
    stcg_pct = rates.stcg_tax_rate * 100
    stt_pct = rates.stt_rate * 100

    notes = [
        f"Pattern ranking uses the last {eval_days} trading days on stored market data (DB only).",
        f"Top {top_patterns_count} patterns (≥{min_hit_rate:g}% hit rate) are ranked for display; "
        f"picks expand to patterns 4–{max_patterns_for_picks}, then lower hit-rate patterns "
        f"(down to 45%), when a tier or price bucket needs at least {min_per_tier} recommendations.",
        "Cap-tier picks are excluded from price-bucket sections — each stock appears in only one "
        "cap tier **or** one price bucket, not both.",
        f"Picks require ≥ ₹{min_expected_move_inr:g} expected move per share "
        "(actual sell − buy from pattern target).",
        f"Net-profit gate: at a ~₹{reference_allocation_inr():,.0f} reference size, "
        f"target is raised up to the model cap until net profit ≥ ₹{min_net_profit_after_tax_inr():g} "
        "after tax and charges; picks skip only when no profitable target fits.",
        "Each cap tier and price bucket tries patterns one-at-a-time (top ranked first) "
        f"until {min_per_tier} profitable picks or patterns are exhausted.",
        f"Volume filter: latest session volume must be ≥ {min_relative_volume:g}× the "
        f"{volume_lookback}-day average; higher relative volume boosts confidence.",
        "Pattern ranking boost (tie-break only, hit rate unchanged in UI): "
        "Swing Structure +9, NR4 +7, Piercing Line +6, Bullish Kicker +6, Engulfing +4; "
        "Falling Wedge 0.",
        "Cap-tier and price-bucket scans use the full NIFTY250 universe (stored OHLCV). "
        "Only symbols in the latest NIFTY250 constituent list are included — delisted or "
        "non-index names (e.g. legacy holdings) are excluded. "
        "Large / mid / small cap groups are assigned from each stock's latest close "
        "(≥ ₹100 / ≥ ₹30 / ≥ ₹10).",
        "All OHLCV comes from the local market-data table — use Refresh market data to update prices.",
        f"Model target is capped at {max_target_pct:g}% profit max. "
        "Actual sell = half of model upside (e.g. model ₹120 → actual ₹110 on ₹100 buy).",
        f"Net profit includes STT ({stt_pct:g}% buy+sell), stamp duty, Sharekhan delivery "
        f"brokerage (0.30%/side), NSE txn + GST, and {stcg_pct:g}% STCG on short-term gains.",
        f"Expected profit* = net profit after tax × pattern {eval_days}-day hit rate.",
        "Profit columns in recommendations are per share at the actual sell price.",
        f"Universe data loaded — Large: {tier_data_counts['large_cap']} · "
        f"Mid: {tier_data_counts['mid_cap']} · Small: {tier_data_counts['small_cap']} "
        f"(NIFTY250 partitioned by latest close).",
        "Budget is split ~⅓ per cap tier; within each tier, higher-confidence picks get more of that slice.",
    ]
    if not qualified:
        notes.append(
            f"No patterns met the {min_hit_rate:g}% minimum hit rate over the last {eval_days} days — "
            "no recommendations generated."
        )
    elif len(top_patterns) < top_patterns_count:
        notes.append(
            f"Only {len(top_patterns)} pattern(s) cleared the {min_hit_rate:g}% threshold "
            f"(target was top {top_patterns_count})."
        )
    for tier_key, label in (
        ("large_cap", "Large Cap"),
        ("mid_cap", "Mid Cap"),
        ("small_cap", "Small Cap"),
    ):
        loaded = tier_data_counts[tier_key]
        picks = tier_rec_counts[tier_key]
        if loaded == 0:
            notes.append(f"{label}: no OHLCV data — sync market data and re-run.")
        elif picks < min_per_tier:
            notes.append(
                f"{label}: {picks}/{min_per_tier} picks — tried expanded patterns; "
                "no additional profitable setups in this cap tier."
            )
        elif picks == 0:
            notes.append(
                f"{label}: {loaded} stocks scanned, no bullish signal from qualified patterns today."
            )
    for label, count in price_bucket_counts.items():
        if count < min_per_price_bucket:
            notes.append(
                f"{label}: {count}/{min_per_price_bucket} picks — tried expanded patterns; "
                "no additional profitable setups in this price bucket."
            )

    tier_counts = tier_rec_counts

    return RecommendationReport(
        generated_at=date.today(),
        prediction_date=prediction_date or date.today(),
        data_through_date=data_through or date.today(),
        lookback_days=lookback,
        eval_days=eval_days,
        top_patterns=top_patterns,
        recommendations=recommendations,
        tier_counts=tier_counts,
        max_target_profit_pct=max_target_pct,
        notes=notes,
        price_bucket_recommendations=price_bucket_recs,
        price_bucket_counts=price_bucket_counts,
    )


def universe_config() -> dict:
    return _load_universe()


def all_universe_symbols() -> dict[str, list[str]]:
    """Deprecated static lists — cap tiers are derived from NIFTY250 at runtime."""
    return {tier: [] for tier in CAP_TIERS}


def flat_universe_symbols() -> list[str]:
    return sorted(market_universe_symbol_set())


def market_universe_symbol_set() -> frozenset[str]:
    """Current NIFTY250 constituents from the on-disk cache (refreshed on market sync)."""
    from app.services.ingestion import market_data_universe
    from app.services.nifty_universe import get_universe_symbols

    return frozenset(get_universe_symbols(market_data_universe()))


def refresh_market_universe_symbol_set() -> frozenset[str]:
    """Use today's cached NIFTY250 list; fetch from NSE only when cache is stale."""
    from app.services.ingestion import market_data_universe
    from app.services.nifty_universe import ensure_universe_symbols_fresh

    universe = market_data_universe()
    symbols = ensure_universe_symbols_fresh(universe)
    return frozenset(s.upper() for s in symbols if s)


def filter_symbol_data_to_market_universe(
    symbol_data: dict[str, pd.DataFrame],
    *,
    allowed: frozenset[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Drop any symbol outside the current market universe (e.g. delisted / non-NIFTY250)."""
    universe = allowed or market_universe_symbol_set()
    return {sym: df for sym, df in symbol_data.items() if sym.upper() in universe}


def filter_tier_symbol_data_to_market_universe(
    symbol_data_by_tier: dict[str, dict[str, pd.DataFrame]],
    *,
    allowed: frozenset[str] | None = None,
) -> dict[str, dict[str, pd.DataFrame]]:
    universe = allowed or market_universe_symbol_set()
    return {
        tier: filter_symbol_data_to_market_universe(tier_data, allowed=universe)
        for tier, tier_data in symbol_data_by_tier.items()
    }


async def load_universe_candles_from_db(session, *, min_rows: int = 35) -> dict[str, pd.DataFrame]:
    """Load OHLCV for NIFTY250 (same universe as market sync and recommendations)."""
    return await load_market_universe_candles_from_db(session, min_rows=min_rows)


async def load_market_universe_candles_from_db(
    session, *, min_rows: int = 25, allowed: frozenset[str] | None = None
) -> dict[str, pd.DataFrame]:
    """NIFTY250 OHLCV from the local market-data table (current constituents only)."""
    from app.models import Instrument, OhlcvCandle

    universe = allowed or market_universe_symbol_set()
    instruments = (
        await session.scalars(
            select(Instrument).where(
                Instrument.is_active.is_(True),
                Instrument.symbol.in_(universe),
            )
        )
    ).all()
    if not instruments:
        return {}

    symbol_by_id = {inst.id: inst.symbol for inst in instruments}
    rows = (
        await session.scalars(
            select(OhlcvCandle)
            .where(OhlcvCandle.instrument_id.in_(symbol_by_id.keys()))
            .order_by(OhlcvCandle.trade_date.asc())
        )
    ).all()

    grouped: dict[str, list[dict]] = {sym: [] for sym in symbol_by_id.values()}
    for row in rows:
        open_ = finite_float(row.open)
        high = finite_float(row.high)
        low = finite_float(row.low)
        close = finite_float(row.close)
        if open_ is None or high is None or low is None or close is None:
            continue
        grouped[symbol_by_id[row.instrument_id]].append(
            {
                "trade_date": row.trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": int(vol) if (vol := finite_float(row.volume)) is not None and vol >= 0 else 0,
            }
        )

    symbol_data: dict[str, pd.DataFrame] = {}
    for symbol, records in grouped.items():
        if len(records) < min_rows:
            continue
        cleaned = sanitize_ohlcv_dataframe(pd.DataFrame(records))
        if cleaned is not None and len(cleaned) >= min_rows:
            symbol_data[symbol] = cleaned
    return filter_symbol_data_to_market_universe(symbol_data, allowed=universe)


async def load_tier_universe_from_db(session, *, min_rows: int = 35) -> dict[str, dict[str, pd.DataFrame]]:
    """NIFTY250 OHLCV grouped into cap tiers by latest close."""
    all_data = await load_market_universe_candles_from_db(session, min_rows=min_rows)
    return partition_symbol_data_by_tier(all_data)
