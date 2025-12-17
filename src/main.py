# src/main.py

# Loading env variables from .env
from pathlib import Path
from dotenv import load_dotenv
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"  # trading-bot/.env
load_dotenv(dotenv_path=ENV_PATH, override=False)  # loading with explicit path for clarity

from fastapi import FastAPI, APIRouter, HTTPException
import uvicorn
from utils import auth
from utils.logger import logger
from services import signal_service
from models.tradingview_signal import TradingviewSignal
from models.signal import Signal
from models.bot_trade import BotTrade
from db.database import Base, engine
from services.sl_manager import SLManager

router = APIRouter()

# ----------------------------
# SL Manager (Background Task)
# ----------------------------
sl_manager = SLManager()


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
        logger.error(f"Auth error: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid auth")

    # 3) Business Logic: Signal an Bot weitergeben
    try:
        await signal_service.handle_signal(signal)
    except Exception as e:
        logger.error(f"Signal handling error: {e}")
        raise HTTPException(status_code=500, detail="Internal error")

    # 4) Antwort an TradingView
    return {"status": "ok"}


# ----------------------------
# FastAPI App
# ----------------------------
app = FastAPI()
app.include_router(router)

# DB-Tabellen erstellen (falls nicht existieren)
Base.metadata.create_all(bind=engine)


# ----------------------------
# Startup / Shutdown Hooks
# ----------------------------
@app.on_event("startup")
async def on_startup():
    logger.info("[APP] startup: starting SLManager")
    sl_manager.start()


@app.on_event("shutdown")
async def on_shutdown():
    logger.info("[APP] shutdown: stopping SLManager")
    await sl_manager.stop()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
