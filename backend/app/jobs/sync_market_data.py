"""Daily market data sync — run via cron or manually."""

import asyncio
import logging

from app.db.session import AsyncSessionLocal
from app.services.ingestion import sync_latest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    async with AsyncSessionLocal() as session:
        result = await sync_latest(session)
        logger.info("Sync complete: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
