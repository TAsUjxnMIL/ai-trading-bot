"""
- Risk-Management-Regeln
    Beispiele:
    Heute schon 3 Verlust-Trades → keine neuen Trades mehr
    Tagesverlust > 3 % → Handel stoppen
    Kein Handel in bestimmten Uhrzeiten (z. B. vor News, vor Session-Ende)
"""

def get_trades_since(date: datetime) -> list[Trade]: ...
def get_last_n_trades(n: int) -> list[Trade]: ...
def get_daily_stats(date: date) -> DailyStats: ...
