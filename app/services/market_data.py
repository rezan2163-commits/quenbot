"""Market data service for fetching real crypto data from CoinGecko API."""

import httpx

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
DEFAULT_TIMEOUT = 30.0


async def get_prices(
    coin_ids: str = "bitcoin,ethereum,binancecoin",
    vs_currencies: str = "usd,try",
) -> dict:
    """Fetch current prices for given coins."""
    params = {
        "ids": coin_ids,
        "vs_currencies": vs_currencies,
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
        "include_market_cap": "true",
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{COINGECKO_BASE_URL}/simple/price", params=params
        )
        resp.raise_for_status()
        return resp.json()


async def get_market_data(
    vs_currency: str = "usd",
    per_page: int = 20,
    page: int = 1,
    order: str = "market_cap_desc",
) -> list[dict]:
    """Fetch market data for top coins."""
    params = {
        "vs_currency": vs_currency,
        "order": order,
        "per_page": per_page,
        "page": page,
        "sparkline": "true",
        "price_change_percentage": "1h,24h,7d",
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{COINGECKO_BASE_URL}/coins/markets", params=params
        )
        resp.raise_for_status()
        return resp.json()


async def get_price_history(
    coin_id: str = "bitcoin",
    vs_currency: str = "usd",
    days: int = 30,
) -> dict:
    """Fetch historical price data for a coin."""
    params = {
        "vs_currency": vs_currency,
        "days": days,
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{COINGECKO_BASE_URL}/coins/{coin_id}/market_chart",
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def get_coin_detail(coin_id: str = "bitcoin") -> dict:
    """Fetch detailed info for a single coin."""
    params = {
        "localization": "false",
        "tickers": "false",
        "community_data": "false",
        "developer_data": "false",
    }
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(
            f"{COINGECKO_BASE_URL}/coins/{coin_id}", params=params
        )
        resp.raise_for_status()
        return resp.json()


async def get_trending() -> dict:
    """Fetch trending coins."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        resp = await client.get(f"{COINGECKO_BASE_URL}/search/trending")
        resp.raise_for_status()
        return resp.json()
