# Controller zwischen Webhook und Business-Logik (trade_engine.py)
"""
Typische Aufgaben:
Webhook-Payload in ein internes Python-Objekt/Feldstruktur mappen.
Validierung: sind symbol, signal, timestamp sinnvoll?
Signal in der DB speichern.
trade_engine aufrufen, um zu entscheiden, ob gehandelt werden soll.

Verbindungen:
Aufgerufen von main.py
Verwendet models.py und db.py, um das Signal zu speichern
Ruft trade_engine.py auf
Nutzt logger.py für Logs
"""

# src/services/signal_service.py

from utils.logger import logger
from models.tradingview_signal import TradingviewSignal  # Pydantic
from models.signal import Signal                  # SQLAlchemy-Model + Session
from db.database import SessionLocal              # DB-Session
from . import trade_engine

async def handle_signal(tv_signal: TradingviewSignal):
    """
    1) TradingView-Signal loggen
    2) In DB speichern (als Signal-Entity)
    3) Später: Order/Trade-Logik anstoßen
    """
    logger.info(f"Received signal (model): {tv_signal.model_dump()}")

    db = SessionLocal()
    try:
        # 1) Aus TradingviewSignal ein Signal-DB-Objekt bauen
        db_signal = Signal.from_tradingview(tv_signal)
        # 2) Speichern
        db.add(db_signal)
        db.commit()
        db.refresh(db_signal)
        signal_id = int(db_signal.id)
        logger.info(f"Saved signal in DB with id={db_signal.id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error while saving signal: {e}")
        raise
    finally:
        db.close()

    # 3) Hier direkt deine Trade-Engine rufen:
    await trade_engine.process_signal(tv_signal, signal_id=signal_id)
    