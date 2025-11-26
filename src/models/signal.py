from datetime import datetime
from pydantic import BaseModel

# Das Signal vom Trading View Webhook
class Signal(BaseModel):
    id: int | None = None # Primärschlüssel. Eindeutige Identifikation in DB
    external_id: str  # Eindeutige ID vom TradingView Webhook
    timestamp: datetime # Zeitstempel des Signals für Backtesting,...
    symbol: str # XAUUSD
    side: str           # "long" oder "short"
    timeframe: str      # "15m", "1h", "4h"
    price: float        # Close-Preis bei Signal
    raw_payload: dict   # Original TradingView JSON: Vollständiger Payload
    processed: bool = False # Wurde das Signal bereits verarbeitet?