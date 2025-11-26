from datetime import datetime
from pydantic import BaseModel

class Order(BaseModel):
    id: int | None = None # Eindeutige Identifikation in DB
    signal_id: int # Welches Signal führte zu dieser Order
    symbol: str # XAUUSD: Brauchen wir für Broker
    side: str               # "buy" / "sell"
    size: float             # Lotgröße: Lot-Berechnung
    # "market": Kauf oder Verkauf zum gesetzten Marktpreis und Schwankungen in Ordnung
    # "limit": Kauf oder Verkauf nur zu einem bestimmten Preis oder besser
    order_type: str # "market" / "limit"
    sl: float | None = None # stop loss Preis
    tp: float | None = None # take profit Preis
    # Hat der Broker die Order ausgeführt?...
    status: str = "pending" # "pending", "filled", "canceled", "sent"
    created_at: datetime = datetime.utcnow() # Zeitstempel der Order-Erstellung
    broker_order_id: str | None = None # ID der Order vom Broker
