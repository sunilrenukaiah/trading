import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.backtest import router as backtest_router
from app.api.routes.market import admin_router, paper_router, router as market_router
from app.db.session import AsyncSessionLocal
from app.logging_setup import configure_app_logging
from app.middleware.audit import AuditMiddleware
from app.services.audit_handlers import install_audit_hooks
from app.services.ingestion import seed_instruments, seed_paper_account

logger = logging.getLogger(__name__)

app = FastAPI(title="NIFTY Paper Trading API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditMiddleware)

app.include_router(market_router, prefix="/api")
app.include_router(paper_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_seed():
    import asyncio

    configure_app_logging()
    install_audit_hooks(asyncio.get_running_loop())
    async with AsyncSessionLocal() as session:
        await seed_instruments(session)
        await seed_paper_account(session)
    logger.info("Startup seed complete — use Refresh market data to pull OHLCV from NSE")
