"""API routes for the QuenBot dashboard."""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.services import market_data
from app.strategy.evolutionary import evolutionary_algorithm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/prices")
async def prices(
    coins: str = Query("bitcoin,ethereum,binancecoin"),
    vs: str = Query("usd,try"),
):
    """Return current prices for the requested coins."""
    try:
        data = await market_data.get_prices(coin_ids=coins, vs_currencies=vs)
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.exception("Failed to fetch prices")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/market")
async def market(
    vs: str = Query("usd"),
    per_page: int = Query(20, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    """Return market overview data."""
    try:
        data = await market_data.get_market_data(
            vs_currency=vs, per_page=per_page, page=page
        )
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.exception("Failed to fetch market data")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/history/{coin_id}")
async def history(
    coin_id: str,
    vs: str = Query("usd"),
    days: int = Query(30, ge=1, le=365),
):
    """Return historical price data for a coin."""
    try:
        data = await market_data.get_price_history(
            coin_id=coin_id, vs_currency=vs, days=days
        )
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.exception("Failed to fetch price history")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/coin/{coin_id}")
async def coin_detail(coin_id: str):
    """Return detailed information for a single coin."""
    try:
        data = await market_data.get_coin_detail(coin_id=coin_id)
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.exception("Failed to fetch coin detail")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/trending")
async def trending():
    """Return currently trending coins."""
    try:
        data = await market_data.get_trending()
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.exception("Failed to fetch trending data")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/strategy/{coin_id}")
async def run_strategy(
    coin_id: str,
    vs: str = Query("usd"),
    days: int = Query(30, ge=1, le=365),
    generations: int = Query(50, ge=10, le=200),
    population: int = Query(50, ge=10, le=200),
):
    """Run the evolutionary trading strategy on historical data."""
    try:
        history_data = await market_data.get_price_history(
            coin_id=coin_id, vs_currency=vs, days=days
        )
        raw_prices = [point[1] for point in history_data.get("prices", [])]
        if len(raw_prices) < 10:
            raise HTTPException(
                status_code=400, detail="Not enough price data to run strategy"
            )

        result = evolutionary_algorithm(
            raw_prices,
            population_size=population,
            generations=generations,
        )
        result["coin_id"] = coin_id
        result["days"] = days
        result["data_points"] = len(raw_prices)
        return {"status": "ok", "data": result}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to run strategy")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
