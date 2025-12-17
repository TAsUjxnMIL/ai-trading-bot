# Implementation of BrokerClient interface for Interactive Brokers (IBKR)
'''
Verbindungen:
trade_engine.py injiziert/benutzt eine Instanz von IbkrClient.
Nutzt config.py für API-URLs/Keys.
Nutzt logger.py, um Requests/Antworten zu loggen.
'''

# src/broker/oanda.py
import httpx
from typing import Optional

from .base import BaseBroker, Side, OrderType
from utils.logger import logger
from config.settings import OANDA_API_KEY, OANDA_ACCOUNT_ID, OANDA_ENV


class OandaBroker(BaseBroker):
    def __init__(self):
        if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
            raise ValueError("OANDA_API_KEY oder OANDA_ACCOUNT_ID fehlen in der .env")

        if OANDA_ENV == "live":
            self.base_url = "https://api-fxtrade.oanda.com/v3"
        else:
            # Default: practice
            self.base_url = "https://api-fxpractice.oanda.com/v3"

        self.api_key = OANDA_API_KEY
        self.account_id = OANDA_ACCOUNT_ID

    def _instrument_from_symbol(self, symbol: str) -> str:
        """
        Mapping TradingView-Symbol -> Oanda-Instrument.
        Beispiel: XAUUSD (TV) -> XAU_USD (Oanda)
        Für andere Paare musst du ggf. manuell ergänzen.
        """
        if symbol.upper() == "XAUUSD":
            return "XAU_USD"
        return symbol  # fallback – später erweitern

    async def place_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        order_type: OrderType = "market",
        price: Optional[float] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> dict:
        instrument = self._instrument_from_symbol(symbol)

        # Oanda: units > 0 für BUY, < 0 für SELL
        units = quantity if side == "buy" else -quantity

        # Oanda erwartet Strings für viele numerische Felder
        order: dict = {
            "instrument": instrument,
            "units": str(units),
            "type": "MARKET",
            "timeInForce": "FOK",  # Fill or Kill
        }

        # MARKET braucht keinen price, aber falls du später LIMIT machst:
        if order_type == "limit" and price is not None:
            order["type"] = "LIMIT"
            order["price"] = f"{price:.2f}"
            order["timeInForce"] = "GTC"

        # TP/SL anhängen
        if take_profit is not None:
            order["takeProfitOnFill"] = {
                "price": f"{take_profit:.2f}"
            }

        if stop_loss is not None:
            order["stopLossOnFill"] = {
                "price": f"{stop_loss:.2f}"
            }

        payload = {"order": order}

        url = f"{self.base_url}/accounts/{self.account_id}/orders"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.info(f"[OANDA] Sending order payload: {payload}")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code >= 400:
            logger.error(
                f"[OANDA] Order error {resp.status_code}: {resp.text}"
            )
            # du kannst hier auch eine spezifische Exception werfen
            raise RuntimeError(f"Oanda order failed: {resp.status_code} {resp.text}")

        data = resp.json()
        logger.info(f"[OANDA] Order response: {data}")

        # du kannst das bei Bedarf aufräumen / mappen
        return data

    async def get_open_positions(self, symbol: Optional[str] = None) -> list[dict]:
        # Minimal-Stub – später nice machen
        url = f"{self.base_url}/accounts/{self.account_id}/openPositions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code >= 400:
            logger.error(f"[OANDA] get_open_positions error {resp.status_code}: {resp.text}")
            return []

        data = resp.json()
        return data.get("positions", [])

    async def get_open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        url = f"{self.base_url}/accounts/{self.account_id}/pendingOrders"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code >= 400:
            logger.error(f"[OANDA] get_open_orders error {resp.status_code}: {resp.text}")
            return []

        data = resp.json()
        return data.get("orders", [])
