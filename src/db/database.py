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

# src/db.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# einfache SQLite-DB im Projektordner
SQLALCHEMY_DATABASE_URL = "sqlite:///./trading_bot.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},  # für SQLite + FastAPI
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
