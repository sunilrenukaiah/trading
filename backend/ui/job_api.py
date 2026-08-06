"""Stable import surface for background jobs (handles stale Streamlit module cache)."""

from __future__ import annotations

import importlib

import ui.background_jobs as _bg

_REQUIRED = (
    "JobKind",
    "cancel_running_job",
    "is_any_job_running",
    "is_kind_running",
    "list_jobs",
    "poll_running_jobs",
    "render_sidebar_job_status",
    "run_background_job_watcher",
    "start_market_sync_job",
    "start_midday_recommendations_job",
    "start_recommendations_job",
    "start_sim_backtest_job",
    "start_today_prediction_job",
    "sync_jobs_to_session",
)

if not all(hasattr(_bg, name) for name in _REQUIRED):
    _bg = importlib.reload(_bg)

JobKind = _bg.JobKind
cancel_running_job = _bg.cancel_running_job
is_any_job_running = _bg.is_any_job_running
is_kind_running = _bg.is_kind_running
list_jobs = _bg.list_jobs
poll_running_jobs = _bg.poll_running_jobs
render_sidebar_job_status = _bg.render_sidebar_job_status
run_background_job_watcher = _bg.run_background_job_watcher
start_market_sync_job = _bg.start_market_sync_job
start_midday_recommendations_job = _bg.start_midday_recommendations_job
start_recommendations_job = _bg.start_recommendations_job
start_sim_backtest_job = _bg.start_sim_backtest_job
start_today_prediction_job = _bg.start_today_prediction_job
sync_jobs_to_session = _bg.sync_jobs_to_session

__all__ = list(_REQUIRED)
