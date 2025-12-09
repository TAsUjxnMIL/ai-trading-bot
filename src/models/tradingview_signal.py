# src/schemas/tradingview.py
from pydantic import BaseModel
from typing import Literal

class TradingviewSignal(BaseModel):
    secret: str
    symbol: str
    side: Literal["long", "short"]
    timeframe: str
    price: float