# Herzstück: Enthält die Strategie
"""
Wir erhalten von TradingView: Signal: BUY XAUUSD ...
Hier entscheiden wir folgendes:
- Haben wir schon eine Position?
    Wenn du z. B. schon LONG bist und ein neues BUY kommt:
    ignorieren?
    aufstocken (Position größer machen)?
    Wenn du SHORT bist und ein BUY kommt:
    Short schließen und Long eröffnen?
    nur schließen, ohne neuen Long?
- Wie groß soll die Position sein?
    TradingView sagt nicht: „Kaufe 0,37 Lot“
    trade_engine.py kann z. B. sagen:
    Risiko pro Trade = 1 % vom Konto
    SL-Abstand = 10 Dollar → daraus Menge berechnen
    Ergebnis: quantity = 0.53 Lot o.ä.
- Stop-Loss / Take-Profit / Order-Typ
    Setzt du SL/TP fest oder handelst du nur mit Market Orders?
    Legst du einen trailing Stop fest?
    Machst du Teilverkäufe (z. B. halbe Position bei +1R schließen)?
- Mode / Umgebung
    Bist du gerade im Paper-Mode oder Live-Mode?
    Welcher Broker wird genutzt? (OANDA, IBKR, IG …)
    Auf welchem Konto handelst du?
- Mehrere Strategien / Signale
    Vielleicht hast du später 2–3 verschiedene TradingView-Skripte.
    trade_engine.py kann:
    bestimmte Strategien priorisieren
    Konflikte lösen (eine sagt BUY, andere SELL)
    pro Strategie eigene Positionsgröße/Risiko haben
"""

def process_signal(signal: Signal):
    # Wir brauchen die Positionen vom Broker
    # So vermeiden wir doppeltes Öffnen derselben Position
    positions = broker_client.get_positions()
    if not risk_manager.check_can_open_position(signal, positions):
        return
    
    order = build_order(signal, positions)
    trade = broker_client.place_order(order)
    db.save_trade(trade)


def save_order(order: Order) -> Order: ...
def update_order_status(order_id: int, status: str, broker_order_id: str | None = None): ...

def save_trade(trade: Trade) -> Trade: ...
def get_open_trades_for_symbol(symbol: str) -> list[Trade]: ...
def get_trades_for_signal(signal_id: int) -> list[Trade]: ...
