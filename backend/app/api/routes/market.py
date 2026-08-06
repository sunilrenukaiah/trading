from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models import Instrument, OhlcvCandle, PaperOrder
from app.schemas import CandleOut, InstrumentOut, MarketSummaryItem, PlaceOrderRequest
from app.schemas.audit import AuditLogOut
from app.services.audit import list_audit_logs
from app.services.audit_types import AuditStatus
from app.services.market_summary import fetch_market_summary
from app.services.ohlcv_utils import finite_decimal
from app.services.ingestion import sync_latest
from app.services.paper_trading import PaperTradingService

router = APIRouter()


@router.get("/instruments", response_model=list[InstrumentOut])
async def list_instruments(db: AsyncSession = Depends(get_db)):
    instruments = (
        await db.scalars(
            select(Instrument)
            .where(Instrument.is_active.is_(True))
            .order_by(Instrument.instrument_type, Instrument.symbol)
        )
    ).all()
    return instruments


@router.get("/instruments/{symbol}/candles", response_model=list[CandleOut])
async def get_candles(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    instrument = await db.scalar(
        select(Instrument).where(Instrument.symbol == symbol.upper())
    )
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    start_date = date.today() - timedelta(days=days)
    candles = (
        await db.scalars(
            select(OhlcvCandle)
            .where(
                OhlcvCandle.instrument_id == instrument.id,
                OhlcvCandle.trade_date >= start_date,
            )
            .order_by(OhlcvCandle.trade_date.asc())
        )
    ).all()
    out: list[CandleOut] = []
    for c in candles:
        open_ = finite_decimal(c.open)
        high = finite_decimal(c.high)
        low = finite_decimal(c.low)
        close = finite_decimal(c.close)
        if open_ is None or high is None or low is None or close is None:
            continue
        out.append(
            CandleOut(
                trade_date=c.trade_date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=c.volume,
            )
        )
    return out


@router.get("/market/summary", response_model=list[MarketSummaryItem])
async def market_summary(db: AsyncSession = Depends(get_db)):
    return await fetch_market_summary(db)


paper_router = APIRouter(prefix="/paper", tags=["paper"])


@paper_router.get("/account")
async def get_account(db: AsyncSession = Depends(get_db)):
    service = PaperTradingService(db)
    return await service.get_account_summary()


@paper_router.get("/positions")
async def get_positions(db: AsyncSession = Depends(get_db)):
    service = PaperTradingService(db)
    return await service.list_positions()


@paper_router.get("/orders")
async def get_orders(db: AsyncSession = Depends(get_db)):
    service = PaperTradingService(db)
    return await service.list_orders()


@paper_router.get("/trades")
async def get_trades(db: AsyncSession = Depends(get_db)):
    service = PaperTradingService(db)
    return await service.list_trades()


@paper_router.post("/orders")
async def place_order(request: PlaceOrderRequest, db: AsyncSession = Depends(get_db)):
    service = PaperTradingService(db)
    try:
        order = await service.place_order(request)
        order = await db.scalar(
            select(PaperOrder)
            .where(PaperOrder.id == order.id)
            .options(selectinload(PaperOrder.instrument))
        )
        return service.order_to_schema(order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@paper_router.delete("/orders/{order_id}")
async def cancel_order(order_id: int, db: AsyncSession = Depends(get_db)):
    service = PaperTradingService(db)
    try:
        order = await service.cancel_order(order_id)
        order = await db.scalar(
            select(PaperOrder)
            .where(PaperOrder.id == order.id)
            .options(selectinload(PaperOrder.instrument))
        )
        return service.order_to_schema(order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.post("/sync")
async def trigger_sync(db: AsyncSession = Depends(get_db)):
    result = await sync_latest(db)
    return {"status": "ok", **result}


@admin_router.get("/audit-logs", response_model=list[AuditLogOut])
async def get_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    action_prefix: str | None = Query(default=None),
    status: AuditStatus | None = Query(default=None),
    component: str | None = Query(default=None),
):
    rows = await list_audit_logs(
        limit=limit,
        action_prefix=action_prefix,
        status=status,
        component=component,
    )
    return rows
