import asyncio

from app.db.session import AsyncSessionLocal
from app.services.ingestion import seed_instruments, seed_paper_account


async def main():
    async with AsyncSessionLocal() as session:
        await seed_instruments(session)
        await seed_paper_account(session)
        print("Bootstrap complete: seeded instruments and paper account.")
        print("Run Refresh market data (UI) or POST /api/admin/sync to pull OHLCV from NSE.")


if __name__ == "__main__":
    asyncio.run(main())
