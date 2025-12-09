# Definiert Tabellen für die DB
# Signal: Emfangene Trading View Signale
# Trade: Eröffnete Trades
# Position: Offene Positionen
# AccountSnapshot: Momentaufnahme des Accounts

"""
signal_service.py: Speichert neue Signale in Signal
trade_engine.py: Öffnet/Schließt Trades, speichert in Trade und Position
risk_manager.py: Überwacht Risiko, nutzt AccountSnapshot
Broker-Client: Speichert hier Positionsdaten


Ohne DB weiß man folgendes nicht:
- Welche Signale wurden empfangen?
- Welche Trades sind offen?
- Welche Trades wurden gemacht?
- Welche Positionen sind offen?
"""