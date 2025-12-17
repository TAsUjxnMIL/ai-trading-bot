# src/broker/dummy.py
import uuid
from .base import BaseBroker, Side, OrderType
from utils.logger import logger

class DummyBroker(BaseBroker):
    async def place_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        order_type: OrderType = "market",
        price: float | None = None,
        take_profit: float | None = None,
        stop_loss: float | None = None,
    ) -> dict:
        order_id = str(uuid.uuid4())
        logger.info(
            f"[DUMMY BROKER] place_order: {side} {quantity} {symbol} "
            f"({order_type}, price={price}, TP={take_profit}, SL={stop_loss}) "
            f"-> order_id={order_id}"
        )
        return {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "price": price,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "status": "filled",   # so tun, als wäre alles sofort gefillt
        }

    async def get_open_positions(self, symbol: str | None = None) -> list[dict]:
        logger.info(f"[DUMMY BROKER] get_open_positions(symbol={symbol})")
        return []

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        logger.info(f"[DUMMY BROKER] get_open_orders(symbol={symbol})")
        return []