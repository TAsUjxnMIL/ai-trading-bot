# src/worker.py
from pathlib import Path
from dotenv import load_dotenv
import asyncio
import signal

# Loading env variables from .env (same logic as main.py)
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

import models
from utils.logger import logger
from services.trade_lifecycle_mgr import TradeLifeCycleManager


async def main():
    logger.info("[WORKER] starting TradeLifeCycleManager")
    tlm = TradeLifeCycleManager()
    tlm.start()

    stop_event = asyncio.Event()

    def _request_stop():
        logger.info("[WORKER] stop requested (signal)")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows fallback (ok)
            pass

    try:
        while not stop_event.is_set():
            await asyncio.sleep(5)
    finally:
        logger.info("[WORKER] stopping TradeLifeCycleManager")
        await tlm.stop()
        logger.info("[WORKER] stopped")


if __name__ == "__main__":
    asyncio.run(main())