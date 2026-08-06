"""Stable import surface for BacktestEngine (handles stale Streamlit module cache)."""

from __future__ import annotations

import importlib
import inspect

import app.services.backtest as _backtest

_REQUIRED_INIT_PARAMS = frozenset({"lookback_days", "eval_days", "symbols", "universe"})


def _ensure_fresh() -> None:
    global _backtest
    import sys

    live = sys.modules.get("app.services.backtest")
    if live is None or live is not _backtest:
        _backtest = importlib.import_module("app.services.backtest")
        return
    params = inspect.signature(_backtest.BacktestEngine.__init__).parameters
    if not _REQUIRED_INIT_PARAMS.issubset(params):
        _backtest = importlib.reload(_backtest)


def BacktestEngine(*args, **kwargs):  # noqa: N802 — factory mirroring class name
    _ensure_fresh()
    return _backtest.BacktestEngine(*args, **kwargs)


def get_backtest_module():
    _ensure_fresh()
    return _backtest


__all__ = ["BacktestEngine", "get_backtest_module"]
