# Wir empfangen Webhook Anfragen von Trading View
# JSON Check -> Authentification passt? 
# JA ? JSON -> Business Logic / Trading Bot
"""
Verbindungen:
importiert config.py → um z. B. das Secret oder ENV zu kennen
importiert auth.py → prüft, ob der Request gültig ist
importiert signal_service.py → gibt den validierten Payload weiter
nutzt logger.py → loggt Requests/Fehler
"""

# src/main.py
from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
load_dotenv()

from utils import auth
from utils.logger import logger
from services import signal_service
from models.tradingview_signal import TradingviewSignal
import uvicorn
from db.database import Base, engine

router = APIRouter()

@router.post("/webhook/tradingview")
async def tradingview_webhook(signal: TradingviewSignal):
    """
    Empfängt ein validiertes TradingView-Signal.
    FastAPI wandelt den JSON-Body automatisch in TradingviewSignal.
    """
    # 1) Logging
    logger.info(f"Received webhook payload: {signal.model_dump()}")

    # 2) Authentifizierung / Secret prüfen
    try:
        payload = signal.model_dump()
        auth.verify(payload)
    except HTTPException as e:
        # kommt direkt von auth.verify
        logger.error(f"Auth error: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid auth")

    # 3) Business Logic: Signal an Bot weitergeben
    try:
        signal_service.handle_signal(signal)
    except Exception as e:
        logger.error(f"Signal handling error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")

    # 4) Antwort an TradingView
    return {"status": "ok"}

# FastAPI-App erstellen und Router einhängen
app = FastAPI()
app.include_router(router)

# DB-Tabellen erstellen (falls nicht existieren)
Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


