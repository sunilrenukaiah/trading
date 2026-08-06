"""Refresh applicable tax/charge rates — run via cron or on app startup."""

import asyncio
import logging

from app.services.applicable_rates import refresh_applicable_rates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    from app.services.applicable_rates import refresh_due

    if not refresh_due():
        logger.info("Rates already refreshed today — skipping.")
        return

    rates = refresh_applicable_rates()
    logger.info(
        "Rates refreshed: STCG=%.2f%% STT=%.3f%% stamp=%.4f%% brokerage=%.4f%% sources=%s",
        rates.stcg_tax_rate * 100,
        rates.stt_rate * 100,
        rates.stamp_duty_rate * 100,
        rates.brokerage_rate * 100,
        rates.sources,
    )
    if rates.notes:
        logger.info("Notes: %s", "; ".join(rates.notes))


if __name__ == "__main__":
    asyncio.run(main())
