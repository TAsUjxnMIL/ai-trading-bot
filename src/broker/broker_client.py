# src/broker/broker_client.py
import asyncio
import os
from typing import Optional, Dict, Any, List

from utils.logger import logger
from .ig_client import IGClient

BROKER_MODE = os.getenv("BROKER_MODE", "dummy").strip().lower()

_client = None
_init_lock = asyncio.Lock()


def _is_coro(fn) -> bool:
    """True if fn is async def coroutine function."""
    return asyncio.iscoroutinefunction(fn)


def _create_client_sync():
    """
    Create the underlying broker client (SYNC).
    Runs inside a thread via asyncio.to_thread so we never block the event loop.
    """
    global _client

    if BROKER_MODE == "ig":
        logger.info("[BROKER] Using IGClient")
        return IGClient()

    raise ValueError(f"Unknown BROKER_MODE={BROKER_MODE!r}")


async def _get_client():
    """
    Lazily create and cache the broker client exactly once (even under concurrency).
    """
    global _client
    if _client is not None:
        return _client

    async with _init_lock:
        if _client is not None:
            return _client

        _client = await asyncio.to_thread(_create_client_sync)
        return _client


# -------------------------------------------------
# Public async API used by the bot
# -------------------------------------------------

async def place_market_order(
    symbol: str,
    side: str,
    size: float,
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
) -> Dict[str, Any]:
    client = await _get_client()
    fn = getattr(client, "place_market_order")

    if _is_coro(fn):
        return await fn(symbol, side, size, take_profit, stop_loss)

    return await asyncio.to_thread(fn, symbol, side, size, take_profit, stop_loss)


async def update_stop_loss(
    deal_id: str,
    new_stop: float,
    take_profit: Optional[float] = None,  # keep in API; we will pass None from TLM so TP is not touched
) -> Dict[str, Any]:
    client = await _get_client()
    fn = getattr(client, "update_stop_loss")

    if _is_coro(fn):
        return await fn(deal_id, new_stop, take_profit)

    return await asyncio.to_thread(fn, deal_id, new_stop, take_profit)


async def get_current_price(symbol: str) -> float:
    client = await _get_client()
    fn = getattr(client, "get_current_price")

    if _is_coro(fn):
        return await fn(symbol)

    return await asyncio.to_thread(fn, symbol)


async def get_open_positions() -> List[Dict[str, Any]]:
    client = await _get_client()
    fn = getattr(client, "get_open_positions")

    if _is_coro(fn):
        return await fn()

    return await asyncio.to_thread(fn)


async def fetch_account_activity(
    from_date,   # datetime
    to_date,     # datetime
    detailed: bool = True,
    page_size: int = 500,
) -> List[Dict[str, Any]]:
    client = await _get_client()
    fn = getattr(client, "fetch_account_activity")

    if _is_coro(fn):
        return await fn(from_date, to_date, detailed, page_size)

    return await asyncio.to_thread(fn, from_date, to_date, detailed, page_size)


async def get_bid_offer(symbol: str):
    client = await _get_client()
    fn = getattr(client, "get_bid_offer")
    if _is_coro(fn):
        return await fn(symbol)
    return await asyncio.to_thread(fn, symbol)


async def shutdown():
    global _client
    if _client is None:
        return

    close_fn = getattr(_client, "close", None)
    if close_fn:
        if _is_coro(close_fn):
            await close_fn()
        else:
            await asyncio.to_thread(close_fn)

    _client = None
