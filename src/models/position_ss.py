# Analyse der aktuellen Position zu einem bestimmten Zeitpunkt
class PositionSnapshot:
    id: int
    taken_at: datetime
    symbol: str
    side: str
    size: float
    avg_entry_price: float
    unrealized_pnl: float
