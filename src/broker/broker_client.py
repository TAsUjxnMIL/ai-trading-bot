# src/broker/broker_client.py

import asyncio
from typing import Optional, Dict, Any, List

from config.settings import BROKER_MODE
from utils.logger import logger

from .dummy import DummyBroker
from .oanda import OandaBroker
from .ig_client import IGClient

# -------------------------------------------------
# Einen konkreten Broker auswählen (Factory)
# -------------------------------------------------

if BROKER_MODE == "dummy":
    logger.info("[BROKER] Using DummyBroker")
    _client = DummyBroker()

elif BROKER_MODE == "oanda":
    logger.info("[BROKER] Using OandaBroker")
    _client = OandaBroker()

elif BROKER_MODE == "real":
    logger.info("[BROKER] Using IGClient (REAL / PRACTICE je nach IG_ENV)")
    _client = IGClient()

else:
    raise ValueError(f"Unknown BROKER_MODE={BROKER_MODE!r}")


def _is_coro(fn) -> bool:
    """Hilfsfunktion: ist die Client-Methode async oder sync?"""
    return asyncio.iscoroutinefunction(fn)


# -------------------------------------------------
# Öffentliche async-API, die dein Bot verwendet
# -------------------------------------------------

async def place_market_order(
    symbol: str,
    side: str,        # "buy" oder "sell"
    size: float,
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Allgemeine Market-Order-Funktion.
    Ruft intern _client.place_market_order(...) auf.
    """
    fn = getattr(_client, "place_market_order")

    if _is_coro(fn):
        return await fn(symbol, side, size, take_profit, stop_loss)
    else:
        return await asyncio.to_thread(
            fn,
            symbol,
            side,
            size,
            take_profit,
            stop_loss,
        )


async def update_stop_loss(
    deal_id: str,
    new_stop: float,
) -> Dict[str, Any]:
    fn = getattr(_client, "update_stop_loss")

    if _is_coro(fn):
        return await fn(deal_id, new_stop)
    else:
        return await asyncio.to_thread(fn, deal_id, new_stop)


async def get_current_price(symbol: str) -> float:
    fn = getattr(_client, "get_current_price")

    if _is_coro(fn):
        return await fn(symbol)
    else:
        return await asyncio.to_thread(fn, symbol)


async def get_open_positions() -> List[Dict[str, Any]]:
    fn = getattr(_client, "get_open_positions")

    if _is_coro(fn):
        return await fn()
    else:
        return await asyncio.to_thread(fn)