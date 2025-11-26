# Abstraktes Interface für Broker-Implementierungen
# trade_engine arbeitet nur gegen dieses Interface
# Konkrete Implementierung dadurch variabel
from typing import Protocol

class BrokerClient(Protocol):
    def place_order(order: Order) -> Trade:
    # sendet Order an Broker-API
    # gibt Trade-Objekt zurück
        ...
    
    def get_positions() -> List[Position]:
        # holt offene Trades
        # Welche Größe, Entry-Preis, SL/TP, etc.
        # Hat Broker Order rejected
        # In welche Richtung hat der Bot bereits investiert?
        ...
        
    def close_order(id: str):
        # optional
        ...
