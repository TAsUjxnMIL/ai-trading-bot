# Baut Verbindung zur Datenbank auf
# Hat get_session()
# signal_service.py, trade_engine.py, risk_manager.py
# nutzen diese Datei um auf die Datenbank zuzugreifen
"""
Wieso brauchen wir eine Datenbank?
    Crash & Neustart Sicherheit:
    - Nach einem Neustart des Bots, wüsste man nicht welche Signale reinkamen
    - Welche Trades wir aufgrund welcher Signale eröffneten
    - Wir können prüfen, ob wir für Signal mit signal_id = 123
      schon einen Trade eröffneten (Dubletten vermeiden)

    Doppelte/Fehlerhafte Signale abfangen:
    - Nur ein Trade pro Kerze
      
    Debugging & Transparenz:
    - Ohne DB, weiß man nicht warum ich da plötzlich im Trade war
    - Am 12.03. um 09:15 kam ein LONG-Signal, Risk-Manager sagte ok,
      Order wurde um 09:15:01 gesendet, Broker hat sofort ausgeführt.“

    Performance Auswertung:
    - Wie viel Gewinn und Verlust pro Tag
    - Winrate/ Drowdown∏

    Risiko-Management:
    - Risk-Manager schaut in die DB, um:
        - Wie viele Verlust-Trades heute schon?
        - Wie viel Verlust heute schon insgesamt?
        - Max. 3 Trades pro Tag...
      """

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # erlaubt dictionary-ähnliche Rückgaben
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Tabelle: Signals
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        external_id TEXT,
        timestamp TEXT,
        symbol TEXT,
        side TEXT,
        timeframe TEXT,
        price REAL,
        raw_payload TEXT,
        processed INTEGER DEFAULT 0
    );
    """)

    # Tabelle: Orders
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        symbol TEXT,
        side TEXT,
        size REAL,
        order_type TEXT,
        sl REAL,
        tp REAL,
        status TEXT,
        created_at TEXT,
        broker_order_id TEXT,
        FOREIGN KEY(signal_id) REFERENCES signals(id)
    );
    """)

    # Tabelle: Trades
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        symbol TEXT,
        side TEXT,
        size REAL,
        entry_price REAL,
        opened_at TEXT,
        closed_at TEXT,
        exit_price REAL,
        pnl REAL,
        broker_trade_id TEXT,
        FOREIGN KEY(order_id) REFERENCES orders(id)
    );
    """)

    conn.commit()
    conn.close()


# Diese Funktion wird einmal beim Start aufgerufen
if __name__ == "__main__":
    init_db()
    print("Database initialized:", DB_PATH)