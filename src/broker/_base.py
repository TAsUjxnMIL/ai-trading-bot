# Abstraktes Interface für Broker-Implementierungen
# trade_engine arbeitet nur gegen dieses Interface
# Konkrete Implementierung dadurch variabel

# src/broker/base.py
from abc import ABC, abstractmethod
from typing import Optional, Literal

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]

class BaseBroker(ABC):
    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        order_type: OrderType = "market",
        price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> dict:
        """
        Schickt eine Order zum Broker und gibt die API-Response zurück.
        `take_profit` und `stop_loss` sind optional und werden, falls unterstützt,
        als TP/SL-Level gesetzt.
        """
        ...

    @abstractmethod
    async def get_open_positions(self, symbol: Optional[str] = None) -> list[dict]:
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        ...
