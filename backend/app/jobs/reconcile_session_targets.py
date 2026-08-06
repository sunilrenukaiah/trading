"""One-off: reconcile OPEN bracket plans against NSE session high/low for today."""

from __future__ import annotations

import asyncio
import json
import logging

from app.db.session import AsyncSessionLocal
from app.services.trade_plans import TradePlanService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    async with AsyncSessionLocal() as session:
        stats = await TradePlanService(session).reconcile_open_plans_with_nse_day_ohlc()
        logger.info("Reconcile complete: %s", json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
