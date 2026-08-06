"""Recommendation engine pattern selection rules."""

from __future__ import annotations

import pandas as pd
import pytest
from datetime import date, datetime

from app.services.recommendation_engine import (
    PatternRanking,
    build_recommendations,
    qualified_pattern_rankings,
    recommendation_pattern_rankings,
    select_top_patterns,
)
from app.strategies.base import Signal
from app.services.backtest import BacktestEngine


def _ranking(pid: str, name: str, hit: float) -> PatternRanking:
    return PatternRanking(
        pattern_id=pid,
        pattern_name=name,
        hit_rate_pct=hit,
        total_correct=int(hit),
        total_signals=10,
        avg_daily_score=0.5,
    )


@pytest.mark.quick
def test_pattern_ranking_boost_reorders_rankings() -> None:
    from app.services.recommendation_engine import _ranking_sort_key

    rankings = [
        _ranking("p_other", "Other", 62.0),
        _ranking("cs_bullish_kicker", "Bullish Kicker", 58.0),
        _ranking("p9_swing_structure", "Swing Structure (5-day)", 56.0),
    ]
    rankings.sort(key=_ranking_sort_key, reverse=True)
    assert [r.pattern_id for r in rankings] == [
        "p9_swing_structure",
        "cs_bullish_kicker",
        "p_other",
    ]


@pytest.mark.quick
def test_select_top_patterns_enforces_min_hit_rate() -> None:
    rankings = [
        _ranking("p1", "A", 70.0),
        _ranking("p2", "B", 52.0),
        _ranking("p3", "C", 60.0),
        _ranking("p4", "D", 58.0),
    ]
    top = select_top_patterns(rankings, min_hit_rate=55.0, top_n=3)
    assert [p.pattern_id for p in top] == ["p1", "p3", "p4"]


@pytest.mark.quick
def test_select_top_patterns_returns_at_most_three() -> None:
    rankings = [_ranking(f"p{i}", f"P{i}", 80 - i) for i in range(6)]
    top = select_top_patterns(rankings, min_hit_rate=55.0, top_n=3)
    assert len(top) == 3
    assert top[0].hit_rate_pct == 80.0


@pytest.mark.quick
def test_build_recommendations_uses_only_qualified_top_patterns() -> None:
    """Stocks signalling on a sub-threshold pattern must not be recommended."""
    import pandas as pd

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    # Declining then flat — many patterns may fire; we only pass one qualified pattern id.
    closes = [100 - i * 0.5 for i in range(25)]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000] * 25,
        }
    )

    qualified = [_ranking("cs_bullish_harami", "Bullish Harami", 62.0)]
    recs, price_buckets, _, _ = build_recommendations(
        {"large_cap": {"INFY": df}},
        qualified,
        min_hit_rate=55.0,
        min_per_tier=1,
        max_per_tier=3,
        min_per_price_bucket=0,
        max_per_price_bucket=0,
    )
    for rec in recs:
        assert rec.pattern_id == "cs_bullish_harami"
        assert rec.pattern_hit_rate_30d >= 55.0


@pytest.mark.quick
def test_build_recommendations_prediction_date_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    import pandas as pd

    from app.services.market_calendar import IST, recommendation_prediction_date

    dates = pd.bdate_range("2026-06-01", "2026-07-30")
    closes = [100.0 + i * 0.1 for i in range(len(dates))]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000] * len(dates),
        }
    )
    qualified = [_ranking("p1", "Hammer", 60.0)]

    fixed_now = datetime(2026, 7, 30, 17, 0, tzinfo=IST)

    def _prediction_date_with_fixed_clock(data_through, now=None):
        del now
        return recommendation_prediction_date(data_through, now=fixed_now)

    monkeypatch.setattr(
        "app.services.market_calendar.recommendation_prediction_date",
        _prediction_date_with_fixed_clock,
    )

    _, _, data_through, prediction_date = build_recommendations(
        {"large_cap": {"INFY": df}},
        qualified,
        min_per_tier=0,
        max_per_tier=0,
        min_per_price_bucket=0,
        max_per_price_bucket=0,
    )
    assert data_through is not None
    assert prediction_date == date(2026, 7, 31)


@pytest.mark.quick
def test_build_recommendations_empty_when_no_qualified_patterns() -> None:
    recs, buckets, data_through, prediction_date = build_recommendations(
        {},
        [],
        min_hit_rate=55.0,
    )
    assert recs == []
    assert buckets == {}
    assert data_through is None
    assert prediction_date is None


@pytest.mark.quick
def test_qualified_pattern_rankings_keeps_order() -> None:
    rankings = [_ranking(f"p{i}", f"P{i}", 80 - i * 5) for i in range(5)]
    qualified = qualified_pattern_rankings(rankings, min_hit_rate=55.0)
    assert len(qualified) == 5
    assert qualified[0].pattern_id == "p0"


@pytest.mark.quick
def test_build_recommendations_returns_price_buckets() -> None:
    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    df_cheap = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [50.0] * 25,
            "high": [51.0] * 25,
            "low": [49.0] * 25,
            "close": [50.0] * 25,
            "volume": [1000] * 25,
        }
    )
    qualified = [_ranking("cs_bullish_harami", "Bullish Harami", 62.0)]
    _, buckets, _, _ = build_recommendations(
        {"small_cap": {"CDSL": df_cheap}},
        qualified,
        min_hit_rate=55.0,
        min_per_tier=0,
        max_per_tier=0,
        min_per_price_bucket=0,
        max_per_price_bucket=3,
        price_buckets_inr=[100, 500],
    )
    assert "Below ₹100" in buckets
    assert "Below ₹500" in buckets


@pytest.mark.quick
def test_price_buckets_do_not_repeat_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stock picked in one price bucket must not appear in a later bucket."""
    from app.services import recommendation_engine as engine

    bucket_round = {"n": 0}
    symbols_by_bucket = [
        ["WIPRO", "JUBLFOOD", "KOTAKBANK"],
        ["HDFCBANK", "AXISBANK", "ICICIBANK"],
        ["RELIANCE", "TCS", "INFY"],
    ]
    exclude_log: list[set[str]] = []

    def _fake_collect(_symbol_data, **kwargs):
        if kwargs.get("price_bucket") is None:
            return []
        idx = bucket_round["n"]
        bucket_round["n"] += 1
        exclude = set(kwargs.get("exclude_symbols") or ())
        exclude_log.append(exclude)
        syms = symbols_by_bucket[idx] if idx < len(symbols_by_bucket) else []
        return [
            engine.StockRecommendation(
                symbol=symbol,
                cap_tier="Large Cap",
                pattern_id="p1",
                pattern_name="Hammer",
                pattern_hit_rate_30d=60.0,
                signal="BULLISH",
                action="BUY",
                buy_price=100.0,
                stop_loss=95.0,
                resistance=110.0,
                sell_price=105.0,
                actual_sell_price=102.0,
                model_profit_pct=5.0,
                actual_profit_pct=2.0,
                risk_reward=1.4,
                latest_close=100.0,
                prev_close=99.0,
                expected_move_pct=2.0,
                confidence_score=70.0,
                price_bucket=kwargs.get("price_bucket"),
            )
            for symbol in syms
            if symbol not in exclude
        ]

    monkeypatch.setattr(engine, "_collect_recommendations", _fake_collect)

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [100.0] * 25,
            "high": [101.0] * 25,
            "low": [99.0] * 25,
            "close": [100.0] * 25,
            "volume": [1000] * 25,
        }
    )
    _, buckets, _, _ = engine.build_recommendations(
        {"large_cap": {"INFY": df}},
        [_ranking("p1", "Hammer", 60.0)],
        min_per_tier=0,
        max_per_tier=0,
        min_per_price_bucket=3,
        max_per_price_bucket=3,
        price_buckets_inr=[100, 500, 1000],
    )

    all_symbols = [rec.symbol for recs in buckets.values() for rec in recs]
    assert len(all_symbols) == len(set(all_symbols))
    assert exclude_log[1] == {"WIPRO", "JUBLFOOD", "KOTAKBANK"}
    wipro_buckets = [
        label for label, recs in buckets.items() if any(r.symbol == "WIPRO" for r in recs)
    ]
    assert len(wipro_buckets) == 1


@pytest.mark.quick
def test_cap_tier_picks_excluded_from_price_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stocks in large/mid/small cap sections must not reappear in price buckets."""
    from app.services import recommendation_engine as engine

    bucket_excludes: list[set[str]] = []

    def _fake_collect(_symbol_data, **kwargs):
        if kwargs.get("price_bucket") is None:
            return [
                engine.StockRecommendation(
                    symbol="JUBLFOOD",
                    cap_tier="Small Cap",
                    pattern_id="p1",
                    pattern_name="Hammer",
                    pattern_hit_rate_30d=60.0,
                    signal="BULLISH",
                    action="BUY",
                    buy_price=438.0,
                    stop_loss=420.0,
                    resistance=450.0,
                    sell_price=445.0,
                    actual_sell_price=440.0,
                    model_profit_pct=2.0,
                    actual_profit_pct=1.0,
                    risk_reward=1.2,
                    latest_close=438.0,
                    prev_close=435.0,
                    expected_move_pct=1.0,
                    confidence_score=70.0,
                )
            ]
        bucket_excludes.append(set(kwargs.get("exclude_symbols") or ()))
        return []

    monkeypatch.setattr(engine, "_collect_recommendations", _fake_collect)

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [100.0] * 25,
            "high": [101.0] * 25,
            "low": [99.0] * 25,
            "close": [100.0] * 25,
            "volume": [1000] * 25,
        }
    )
    engine.build_recommendations(
        {"small_cap": {"JUBLFOOD": df}},
        [_ranking("p1", "Hammer", 60.0)],
        min_per_tier=1,
        max_per_tier=3,
        min_per_price_bucket=3,
        max_per_price_bucket=3,
        price_buckets_inr=[500, 1000],
    )

    assert bucket_excludes
    assert "JUBLFOOD" in bucket_excludes[0]


@pytest.mark.quick
def test_sanitize_price_bucket_removes_cap_tier_duplicates() -> None:
    from app.services.recommendation_engine import (
        RecommendationReport,
        StockRecommendation,
        apply_price_bucket_sanitize,
    )

    def _rec(symbol: str, *, tier: str | None = None, bucket: str | None = None) -> StockRecommendation:
        return StockRecommendation(
            symbol=symbol,
            cap_tier=tier or "Small Cap",
            pattern_id="p1",
            pattern_name="Hammer",
            pattern_hit_rate_30d=60.0,
            signal="BULLISH",
            action="BUY",
            buy_price=100.0,
            stop_loss=95.0,
            resistance=110.0,
            sell_price=105.0,
            actual_sell_price=102.0,
            model_profit_pct=5.0,
            actual_profit_pct=2.0,
            risk_reward=1.4,
            latest_close=100.0,
            prev_close=99.0,
            expected_move_pct=2.0,
            confidence_score=70.0,
            price_bucket=bucket,
        )

    report = RecommendationReport(
        generated_at=date(2026, 8, 3),
        prediction_date=date(2026, 8, 4),
        data_through_date=date(2026, 8, 3),
        lookback_days=20,
        eval_days=15,
        top_patterns=[],
        recommendations=[_rec("JUBLFOOD", tier="Small Cap")],
        tier_counts={"small_cap": 1},
        max_target_profit_pct=80.0,
        price_bucket_recommendations={
            "Below ₹500": [_rec("JUBLFOOD", bucket="Below ₹500"), _rec("WIPRO", bucket="Below ₹500")],
            "Below ₹1,000": [_rec("HDFCBANK", bucket="Below ₹1,000")],
        },
    )
    apply_price_bucket_sanitize(report)
    assert [r.symbol for r in report.price_bucket_recommendations["Below ₹500"]] == ["WIPRO"]
    from app.services.recommendation_engine import (
        StockRecommendation,
        dedupe_price_bucket_recommendations,
    )

    def _rec(symbol: str, bucket: str) -> StockRecommendation:
        return StockRecommendation(
            symbol=symbol,
            cap_tier="Large Cap",
            pattern_id="p1",
            pattern_name="Hammer",
            pattern_hit_rate_30d=60.0,
            signal="BULLISH",
            action="BUY",
            buy_price=100.0,
            stop_loss=95.0,
            resistance=110.0,
            sell_price=105.0,
            actual_sell_price=102.0,
            model_profit_pct=5.0,
            actual_profit_pct=2.0,
            risk_reward=1.4,
            latest_close=100.0,
            prev_close=99.0,
            expected_move_pct=2.0,
            confidence_score=70.0,
            price_bucket=bucket,
        )

    raw = {
        "Below ₹500": [_rec("WIPRO", "Below ₹500"), _rec("JUBLFOOD", "Below ₹500")],
        "Below ₹1,000": [
            _rec("WIPRO", "Below ₹1,000"),
            _rec("JUBLFOOD", "Below ₹1,000"),
            _rec("HDFCBANK", "Below ₹1,000"),
        ],
    }
    deduped = dedupe_price_bucket_recommendations(raw)
    assert [r.symbol for r in deduped["Below ₹500"]] == ["WIPRO", "JUBLFOOD"]
    assert [r.symbol for r in deduped["Below ₹1,000"]] == ["HDFCBANK"]


@pytest.mark.quick
def test_rank_patterns_counts_bullish_signals_only() -> None:
    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    closes = [100 - i for i in range(30)]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000] * 30,
        }
    )

    engine = BacktestEngine(eval_days=15, lookback_days=20)
    all_report = engine.run_on_data({"TEST": df})
    bull_report = engine.run_on_data({"TEST": df}, count_signal=Signal.BULLISH)

    all_signals = sum(p.total_signals for p in all_report.patterns)
    bull_signals = sum(p.total_signals for p in bull_report.patterns)
    assert bull_signals <= all_signals


@pytest.mark.quick
def test_recommendation_pattern_rankings_returns_top_three_qualified() -> None:
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    closes = [100 + (i % 5) - 2 for i in range(40)]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1000] * 40,
        }
    )
    _, top = recommendation_pattern_rankings({"INFY": df}, min_hit_rate=0.0, top_n=3)
    assert len(top) <= 3


@pytest.mark.quick
def test_build_recommendations_skips_nan_close() -> None:
    import math

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    closes = [100.0] * 24 + [float("nan")]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 1 if math.isfinite(c) else float("nan") for c in closes],
            "low": [c - 1 if math.isfinite(c) else float("nan") for c in closes],
            "close": closes,
            "volume": [1000] * 25,
        }
    )
    qualified = [_ranking("cs_bullish_harami", "Bullish Harami", 62.0)]
    recs, _, _, _ = build_recommendations(
        {"small_cap": {"CDSL": df}},
        qualified,
        min_hit_rate=55.0,
        min_per_price_bucket=0,
        max_per_price_bucket=0,
    )
    assert recs == []


@pytest.mark.quick
@pytest.mark.asyncio
async def test_load_tier_universe_from_db_avoids_dataframe_or_truthiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `all_data.get(sym) or ...` raises on a non-empty DataFrame."""
    from app.services.recommendation_engine import load_tier_universe_from_db

    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    closes = [1150.0 + i for i in range(40)]
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1000] * 40,
        }
    )

    async def _fake_load(_session, *, min_rows: int = 35):
        del min_rows
        return {"INFY": df}

    monkeypatch.setattr(
        "app.services.recommendation_engine.load_market_universe_candles_from_db",
        _fake_load,
    )

    by_tier = await load_tier_universe_from_db(None)
    assert "INFY" in by_tier["large_cap"]
    assert len(by_tier["large_cap"]["INFY"]) == 40


@pytest.mark.quick
def test_classify_symbol_tier_by_price() -> None:
    from app.services.recommendation_engine import classify_symbol_tier_by_price

    assert classify_symbol_tier_by_price(150.0) == "large_cap"
    assert classify_symbol_tier_by_price(100.0) == "large_cap"
    assert classify_symbol_tier_by_price(50.0) == "mid_cap"
    assert classify_symbol_tier_by_price(30.0) == "mid_cap"
    assert classify_symbol_tier_by_price(15.0) == "small_cap"
    assert classify_symbol_tier_by_price(9.99) is None


@pytest.mark.quick
def test_partition_symbol_data_by_tier() -> None:
    from app.services.recommendation_engine import partition_symbol_data_by_tier

    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    data = {
        "INFY": pd.DataFrame({"trade_date": dates, "close": [500.0] * 30}),
        "SUZLON": pd.DataFrame({"trade_date": dates, "close": [50.0] * 30}),
        "PENNY": pd.DataFrame({"trade_date": dates, "close": [5.0] * 30}),
    }
    by_tier = partition_symbol_data_by_tier(data)
    assert "INFY" in by_tier["large_cap"]
    assert "SUZLON" in by_tier["mid_cap"]
    assert "PENNY" not in by_tier["small_cap"]


@pytest.mark.quick
def test_filter_symbol_data_excludes_non_nifty250() -> None:
    from app.services.recommendation_engine import filter_symbol_data_to_market_universe

    dates = pd.date_range("2026-01-01", periods=30, freq="B")
    data = {
        "INFY": pd.DataFrame({"trade_date": dates, "close": [500.0] * 30}),
        "CDSL": pd.DataFrame({"trade_date": dates, "close": [50.0] * 30}),
    }
    filtered = filter_symbol_data_to_market_universe(data, allowed=frozenset({"INFY"}))
    assert list(filtered) == ["INFY"]


@pytest.mark.quick
def test_sanitize_ohlcv_dataframe_drops_nan_rows() -> None:
    from app.services.ohlcv_utils import sanitize_ohlcv_dataframe

    df = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=3, freq="B"),
            "open": [1.0, float("nan"), 3.0],
            "high": [1.1, 2.1, 3.1],
            "low": [0.9, 1.9, 2.9],
            "close": [1.0, 2.0, 3.0],
            "volume": [100, 100, 100],
        }
    )
    cleaned = sanitize_ohlcv_dataframe(df)
    assert cleaned is not None
    assert len(cleaned) == 2


@pytest.mark.quick
def test_volume_metrics_relative_to_prior_average() -> None:
    from app.services.recommendation_engine import _volume_metrics

    df = pd.DataFrame({"volume": [1000.0] * 20 + [1500.0]})
    rel, score = _volume_metrics(df, lookback=20)
    assert rel == pytest.approx(1.5)
    assert score == 12.0


@pytest.mark.quick
def test_volume_metrics_rejects_thin_liquidity() -> None:
    from app.services.recommendation_engine import _volume_metrics

    df = pd.DataFrame({"volume": [1000.0] * 20 + [500.0]})
    rel, score = _volume_metrics(df, lookback=20)
    assert rel == pytest.approx(0.5)
    assert score == -8.0


@pytest.mark.quick
def test_evaluate_symbol_rejects_expected_move_below_one_inr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import recommendation_engine as engine
    from app.services.trade_tax import SellTargets
    from app.strategies.base import Signal

    class _AlwaysBullish:
        id = "p_test"
        name = "Test Pattern"

        def evaluate(self, _df):
            return Signal.BULLISH

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    closes = [50.0] * 25
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000.0] * 25,
        }
    )

    def _tiny_targets(_buy, _raw, **kwargs):
        del kwargs
        return SellTargets(
            buy_price=_buy,
            model_sell_price=50.5,
            actual_sell_price=50.5,
            model_profit_pct=1.0,
            actual_profit_pct=1.0,
            model_profit_inr=0.5,
            actual_profit_inr=0.5,
        )

    monkeypatch.setattr(engine, "compute_sell_targets", _tiny_targets)

    rec, _ = engine._evaluate_symbol(
        "CDSL",
        df,
        tier_key="small_cap",
        allowed_pattern_ids={"p_test"},
        pattern_hit={"p_test": 60.0},
        pattern_map={"p_test": _AlwaysBullish()},
        lookback=20,
        min_hit_rate=55.0,
        max_target_profit_pct=80.0,
        min_expected_move_inr=1.0,
        min_relative_volume=0.0,
    )
    assert rec is None


@pytest.mark.quick
def test_evaluate_symbol_accepts_expected_move_at_least_one_inr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import recommendation_engine as engine
    from app.services.trade_tax import SellTargets
    from app.strategies.base import Signal

    class _AlwaysBullish:
        id = "p_test"
        name = "Test Pattern"

        def evaluate(self, _df):
            return Signal.BULLISH

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    closes = [500.0] * 25
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1000.0] * 24 + [1800.0],
        }
    )

    def _good_targets(_buy, _raw, **kwargs):
        del kwargs
        return SellTargets(
            buy_price=_buy,
            model_sell_price=520.0,
            actual_sell_price=515.0,
            model_profit_pct=4.0,
            actual_profit_pct=3.0,
            model_profit_inr=20.0,
            actual_profit_inr=15.0,
        )

    monkeypatch.setattr(engine, "compute_sell_targets", _good_targets)

    rec, _ = engine._evaluate_symbol(
        "INFY",
        df,
        tier_key="large_cap",
        allowed_pattern_ids={"p_test"},
        pattern_hit={"p_test": 60.0},
        pattern_map={"p_test": _AlwaysBullish()},
        lookback=20,
        min_hit_rate=55.0,
        max_target_profit_pct=80.0,
        min_expected_move_inr=1.0,
        min_relative_volume=0.75,
    )
    assert rec is not None
    assert rec.expected_move_inr >= 1.0
    assert rec.relative_volume is not None
    assert rec.relative_volume >= 0.75
    assert rec.volume_score > 0


@pytest.mark.quick
def test_nr4_pattern_ranking_boost_in_defaults() -> None:
    from app.services.recommendation_engine import pattern_ranking_boost

    assert pattern_ranking_boost("pa_nr4") == 7.0


@pytest.mark.quick
def test_nr4_confluence_boosts_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import recommendation_engine as engine
    from app.services.trade_tax import SellTargets
    from app.strategies.base import Signal

    class _Nr4:
        id = "pa_nr4"
        name = "NR4 (Narrow Range 4)"

        def evaluate(self, _df):
            return Signal.BULLISH

    class _Other:
        id = "p_other"
        name = "Other Pattern"

        def evaluate(self, _df):
            return Signal.BULLISH

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    closes = [500.0] * 25
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1000.0] * 25,
        }
    )

    def _good_targets(_buy, _raw, **kwargs):
        del kwargs
        return SellTargets(
            buy_price=_buy,
            model_sell_price=510.0,
            actual_sell_price=505.0,
            model_profit_pct=2.0,
            actual_profit_pct=1.0,
            model_profit_inr=10.0,
            actual_profit_inr=5.0,
        )

    monkeypatch.setattr(engine, "compute_sell_targets", _good_targets)

    rec, _ = engine._evaluate_symbol(
        "INFY",
        df,
        tier_key="large_cap",
        allowed_pattern_ids={"pa_nr4", "p_other"},
        pattern_hit={"pa_nr4": 58.0, "p_other": 60.0},
        pattern_map={"pa_nr4": _Nr4(), "p_other": _Other()},
        lookback=20,
        min_hit_rate=55.0,
        max_target_profit_pct=80.0,
        min_expected_move_inr=1.0,
        min_relative_volume=0.0,
    )
    assert rec is not None
    assert rec.nr4_confluence is True
    assert rec.pattern_id == "pa_nr4"
    # 58 NR4 hit + 10 agreement + 4 flat volume + 8 confluence
    assert rec.confidence_score == pytest.approx(80.0)


@pytest.mark.quick
def test_collect_recommendations_scans_one_pattern_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import recommendation_engine as engine

    seen: list[set[str]] = []

    def _capture(symbol, df, **kwargs):
        seen.append(set(kwargs["allowed_pattern_ids"]))
        return None, None

    monkeypatch.setattr(engine, "_evaluate_symbol", _capture)

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [100.0] * 25,
            "high": [101.0] * 25,
            "low": [99.0] * 25,
            "close": [100.0] * 25,
            "volume": [1000.0] * 25,
        }
    )
    rankings = [
        _ranking("p_top", "Top Pattern", 70.0),
        _ranking("pa_nr4", "NR4 (Narrow Range 4)", 56.0),
    ]
    engine._collect_recommendations(
        {"AAA": df},
        tier_key="large_cap",
        qualified_patterns=rankings,
        lookback=20,
        min_hit_rate=55.0,
        min_count=1,
        max_count=1,
        max_target_profit_pct=80.0,
        initial_pattern_count=1,
        max_pattern_count=1,
        fallback_patterns=rankings,
    )
    assert {"p_top"} in seen
    assert len(seen) >= 1
    assert all(len(ids) == 1 for ids in seen)


@pytest.mark.quick
def test_adjust_actual_sell_raises_target_until_profitable() -> None:
    from app.services.recommendation_engine import _adjust_actual_sell_for_net_profit

    # Too tight at 395 — bump should reach profitable level within model cap.
    adjusted = _adjust_actual_sell_for_net_profit(393.0, 395.0, 420.0)
    assert adjusted is not None
    assert adjusted > 395.0
    assert adjusted <= 420.0


@pytest.mark.quick
def test_pattern_exclude_ids_from_universe() -> None:
    from app.services.recommendation_engine import pattern_exclude_ids

    assert "cs_tweezer_bottom" in pattern_exclude_ids()


@pytest.mark.quick
def test_pattern_max_picks_per_day_from_universe() -> None:
    from app.services.recommendation_engine import pattern_max_picks_per_day

    assert pattern_max_picks_per_day().get("p2_rsi_momentum") == 2


@pytest.mark.quick
def test_universe_target_tuning_defaults() -> None:
    from app.services.recommendation_engine import (
        target_atr_multiplier,
        target_resistance_factor,
        universe_default_max_target_profit_pct,
    )

    assert target_atr_multiplier() == 0.35
    assert target_resistance_factor() == 0.85
    assert universe_default_max_target_profit_pct() == 50.0


@pytest.mark.quick
def test_filter_excluded_rankings() -> None:
    from app.services.recommendation_engine import _filter_excluded_rankings

    rankings = [
        _ranking("cs_tweezer_bottom", "Tweezer Bottom", 70.0),
        _ranking("p9_swing_structure", "Swing Structure (5-day)", 65.0),
    ]
    filtered = _filter_excluded_rankings(rankings)
    assert [r.pattern_id for r in filtered] == ["p9_swing_structure"]


@pytest.mark.quick
def test_evaluate_symbol_skips_excluded_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    import pandas as pd

    from app.services import recommendation_engine as engine
    from app.services.trade_tax import SellTargets
    from app.strategies.base import Signal

    class _Tweezer:
        id = "cs_tweezer_bottom"
        name = "Tweezer Bottom"

        def evaluate(self, _df):
            return Signal.BULLISH

    class _Good:
        id = "p9_swing_structure"
        name = "Swing Structure (5-day)"

        def evaluate(self, _df):
            return Signal.BULLISH

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    closes = [500.0] * 25
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1000.0] * 25,
        }
    )

    def _good_targets(_buy, _raw, **kwargs):
        del kwargs
        return SellTargets(
            buy_price=_buy,
            model_sell_price=510.0,
            actual_sell_price=505.0,
            model_profit_pct=2.0,
            actual_profit_pct=1.0,
            model_profit_inr=10.0,
            actual_profit_inr=5.0,
        )

    monkeypatch.setattr(engine, "compute_sell_targets", _good_targets)

    rec, _ = engine._evaluate_symbol(
        "INFY",
        df,
        tier_key="large_cap",
        allowed_pattern_ids={"cs_tweezer_bottom", "p9_swing_structure"},
        pattern_hit={"cs_tweezer_bottom": 80.0, "p9_swing_structure": 60.0},
        pattern_map={"cs_tweezer_bottom": _Tweezer(), "p9_swing_structure": _Good()},
        lookback=20,
        min_hit_rate=55.0,
        max_target_profit_pct=50.0,
        min_relative_volume=0.0,
    )
    assert rec is not None
    assert rec.pattern_id == "p9_swing_structure"


@pytest.mark.quick
def test_evaluate_symbol_rejects_when_net_profit_after_charges_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pandas as pd

    from app.services import recommendation_engine as engine
    from app.services.trade_tax import SellTargets
    from app.strategies.base import Signal

    class _AlwaysBullish:
        id = "p_test"
        name = "Test Pattern"

        def evaluate(self, _df):
            return Signal.BULLISH

    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    closes = [500.0] * 25
    df = pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1000.0] * 25,
        }
    )

    def _tiny_targets(_buy, _raw, **kwargs):
        del kwargs
        return SellTargets(
            buy_price=_buy,
            model_sell_price=_buy + 2.0,
            actual_sell_price=_buy + 1.5,
            model_profit_pct=0.4,
            actual_profit_pct=0.3,
            model_profit_inr=2.0,
            actual_profit_inr=1.5,
        )

    monkeypatch.setattr(engine, "compute_sell_targets", _tiny_targets)

    rec, _ = engine._evaluate_symbol(
        "INFY",
        df,
        tier_key="large_cap",
        allowed_pattern_ids={"p_test"},
        pattern_hit={"p_test": 60.0},
        pattern_map={"p_test": _AlwaysBullish()},
        lookback=20,
        min_hit_rate=55.0,
        max_target_profit_pct=50.0,
        min_expected_move_inr=1.0,
        min_relative_volume=0.0,
    )
    assert rec is None


@pytest.mark.quick
def test_passes_net_profit_gate_requires_positive_reference_net() -> None:
    from app.services.recommendation_engine import (
        net_profit_for_reference_position,
        passes_net_profit_gate,
    )

    assert net_profit_for_reference_position(393.0, 395.0) < 0
    assert passes_net_profit_gate(393.0, 395.0) is False
    assert passes_net_profit_gate(500.0, 520.0) is True

