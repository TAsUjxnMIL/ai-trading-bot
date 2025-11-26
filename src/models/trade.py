from datetime import datetime
from pydantic import BaseModel

# Was hat der Broker tatsächlich ins Depot gelegt?
class Trade(BaseModel):
    id: int | None = None # Eindeutige Identifikation in DB
    order_id: int # Welche Order führte zu diesem Trade
    symbol: str # XAUUSD
    side: str # "buy" / "sell"
    size: float  # Lotgröße
    entry_price: float # Entry-Preis
    opened_at: datetime # Zeitpunkt der Trade-Eröffnung
    closed_at: datetime | None = None # Zeitpunkt der Schließung
    exit_price: float | None = None # Preis beim Close
    pnl: float | None = None # Realisierter Gewinn/Verlust
    broker_trade_id: str | None = None # Broker-interne Trade-ID