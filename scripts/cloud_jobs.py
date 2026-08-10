"""Headless cloud jobs: market sync + daily recommendations (GitHub Actions / CLI).

Usage (from repo root, with DATABASE_URL set):

  python scripts/cloud_jobs.py migrate
  python scripts/cloud_jobs.py market-sync
  python scripts/cloud_jobs.py market-sync --force
  python scripts/cloud_jobs.py recommendations
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("cloud_jobs")


def _progress(current=None, total=None, message=None, *_args, **_kwargs) -> None:
    if isinstance(current, str) and total is None:
        log.info("%s", current)
        return
    if message:
        if current is not None and total:
            log.info("[%s/%s] %s", current, total, message)
        else:
            log.info("%s", message)


def cmd_migrate() -> int:
    log.info("Running alembic upgrade head against DATABASE_URL")
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        check=True,
    )
    log.info("Migrations complete")
    return 0


async def _seed() -> None:
    from app.db.session import AsyncSessionLocal
    from app.services.ingestion import seed_instruments, seed_paper_account

    async with AsyncSessionLocal() as session:
        await seed_instruments(session)
        await seed_paper_account(session)


async def _market_sync(*, force: bool) -> int:
    from app.services.ingestion import sync_latest
    from app.services.market_calendar import IST, is_trading_day
    from app.services.market_sync_status import (
        daily_auto_sync_needed,
        record_market_sync_success,
    )
    from datetime import datetime

    now = datetime.now(IST)
    if not force and not is_trading_day(now.date()):
        log.info("Skipping market sync — not an NSE trading day (%s)", now.date())
        return 0

    await _seed()

    if not force:
        needed = await daily_auto_sync_needed(force=False)
        if not needed:
            log.info("Skipping market sync — today's post-session data already synced")
            return 0

    log.info("Starting market sync (force=%s)", force)

    def progress(current, total, message):
        _progress(current, total, message)

    result = await sync_latest(progress_callback=progress)
    record_market_sync_success(result.get("data_through"))
    log.info(
        "Market sync done inserted=%s data_through=%s",
        result.get("inserted"),
        result.get("data_through"),
    )
    return 0


async def _recommendations(
    *, budget_inr: float, max_target_profit_pct: float | None, force: bool = False
) -> int:
    from datetime import datetime

    from app.db.ui_session import ui_session
    from app.services.budget_allocator import allocate_budget
    from app.services.market_calendar import IST, is_evening_recommendation_ready
    from app.services.recommendation_cache import save_recommendation_snapshot
    from app.services.recommendation_engine import (
        load_market_universe_candles_from_db,
        partition_symbol_data_by_tier,
        refresh_market_universe_symbol_set,
        run_recommendation_engine,
        universe_config,
    )
    from app.services.backtest import min_candles_for_simulation

    now = datetime.now(IST)
    if not force and not is_evening_recommendation_ready(now=now):
        log.info(
            "Skipping recommendations — evening window opens at 6:00 PM IST (now %s)",
            now.strftime("%H:%M %Z"),
        )
        return 0

    await _seed()

    log.info("Refreshing NIFTY universe symbols…")
    allowed = refresh_market_universe_symbol_set()
    cfg = universe_config()
    min_rows = min_candles_for_simulation(
        int(cfg.get("lookback_days", 30)),
        int(cfg.get("eval_days", 30)),
    )

    log.info("Loading candles from database (min_rows=%s)…", min_rows)
    async with ui_session() as session:
        nifty_data = await load_market_universe_candles_from_db(
            session, min_rows=min_rows, allowed=allowed
        )

    if not nifty_data:
        log.error("No candle data loaded — run market-sync first")
        return 1

    tier_data = partition_symbol_data_by_tier(nifty_data)
    log.info("Running recommendation engine on %s symbols…", len(nifty_data))
    report = run_recommendation_engine(
        tier_data,
        ranking_data_by_tier=tier_data,
        bucket_symbol_data=nifty_data,
        max_target_profit_pct=max_target_profit_pct,
        progress_callback=_progress,
    )

    allocation = allocate_budget(
        report,
        budget_inr,
        tier_budget_split_pct=cfg.get("tier_budget_split_pct", 33.33),
    )

    async with ui_session() as session:
        await save_recommendation_snapshot(
            session,
            report,
            allocation,
            budget_inr=budget_inr,
            max_target_profit_pct=max_target_profit_pct or report.max_target_profit_pct,
        )

    log.info(
        "Recommendations saved — picks=%s budget_inr=%s",
        len(allocation.lines),
        budget_inr,
    )
    return 0


def cmd_market_sync(*, force: bool) -> int:
    return asyncio.run(_market_sync(force=force))


def cmd_recommendations(
    *, budget_inr: float, max_target_profit_pct: float | None, force: bool = False
) -> int:
    return asyncio.run(
        _recommendations(
            budget_inr=budget_inr,
            max_target_profit_pct=max_target_profit_pct,
            force=force,
        )
    )


def main(argv: list[str] | None = None) -> int:
    from app.config import settings

    parser = argparse.ArgumentParser(description="Cloud / CI jobs for NIFTY paper trading")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="alembic upgrade head")

    sync_p = sub.add_parser("market-sync", help="Backfill / refresh OHLCV (NSE)")
    sync_p.add_argument(
        "--force",
        action="store_true",
        help="Run even if today's auto-sync already completed",
    )

    rec_p = sub.add_parser("recommendations", help="Daily recommendation snapshot")
    rec_p.add_argument(
        "--budget-inr",
        type=float,
        default=None,
        help="Allocation budget (default: DAILY_TRADING_BUDGET_INR / settings)",
    )
    rec_p.add_argument(
        "--max-target-profit-pct",
        type=float,
        default=None,
        help="Optional max target profit %% cap",
    )
    rec_p.add_argument(
        "--force",
        action="store_true",
        help="Run even before 6:00 PM IST evening window",
    )

    args = parser.parse_args(argv)
    log.info("DATABASE_URL host from settings (password redacted)")
    url = settings.database_url
    safe = url.split("@")[-1] if "@" in url else url
    log.info("DB target: %s", safe)

    if args.command == "migrate":
        return cmd_migrate()
    if args.command == "market-sync":
        return cmd_market_sync(force=args.force)
    if args.command == "recommendations":
        budget = args.budget_inr
        if budget is None:
            budget = float(settings.daily_trading_budget_inr)
        return cmd_recommendations(
            budget_inr=budget,
            max_target_profit_pct=args.max_target_profit_pct,
            force=args.force,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
