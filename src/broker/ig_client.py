# src/broker/ig_client.py
import os
import requests
from typing import Optional, Dict, Any

from dotenv import load_dotenv

# .env einlesen (liegt z.B. im Projekt-Root)
load_dotenv()


class IGClient:
    """
    Sehr einfacher IG REST-Client für DEMO.
    - Loggt sich einmal ein und speichert CST + X-SECURITY-TOKEN + ACCOUNT-ID
    - Kann Market-Orders mit StopLoss und TakeProfit schicken
    - Kann später erweitert werden (SL anpassen, Positionen auslesen etc.)

    Credentials kommen standardmäßig aus .env:
        IG_API_KEY=...
        IG_USERNAME=...
        IG_PASSWORD=...
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        base_url: str = "https://demo-api.ig.com/gateway/deal",
    ) -> None:
        # Wenn nichts übergeben wurde, aus ENV lesen
        self.api_key = api_key or os.getenv("IG_API_KEY")
        self.username = username or os.getenv("IG_USERNAME")
        self.password = password or os.getenv("IG_PASSWORD")
        self.base_url = base_url.rstrip("/")

        if not self.api_key or not self.username or not self.password:
            raise ValueError(
                "Missing IG credentials. Please set IG_API_KEY, "
                "IG_USERNAME and IG_PASSWORD in .env or pass them "
                "explicitly to IGClient()."
            )

        self.cst: Optional[str] = None
        self.xst: Optional[str] = None
        self.account_id: Optional[str] = None

    # --------------------
    # intern: Login & Header
    # --------------------
    def _login(self) -> None:
        """Session bei IG erstellen (CST + X-SECURITY-TOKEN + Account ID)."""
        headers = {
            "X-IG-API-KEY": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "2",
        }
        data = {
            "identifier": self.username,
            "password": self.password,
        }
        resp = requests.post(f"{self.base_url}/session", headers=headers, json=data)
        if resp.status_code != 200:
            raise RuntimeError(
                f"IG login failed: {resp.status_code} {resp.text}"
            )

        self.cst = resp.headers["CST"]
        self.xst = resp.headers["X-SECURITY-TOKEN"]
        body = resp.json()
        self.account_id = body["currentAccountId"]

    def _headers(self) -> Dict[str, str]:
        """Headers für alle weiteren Requests (nach erfolgreichem Login)."""
        if not (self.cst and self.xst and self.account_id):
            self._login()

        return {
            "X-IG-API-KEY": self.api_key,
            "CST": self.cst,
            "X-SECURITY-TOKEN": self.xst,
            "IG-ACCOUNT-ID": self.account_id,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "Version": "2",
        }

    # --------------------
    # Symbol-Mapping
    # --------------------
    @staticmethod
    def _symbol_to_epic(symbol: str) -> str:
        """
        Mapping TradingView-Symbol -> IG-EPIC.
        Hier kannst du später mehr Symbole ergänzen.
        """
        symbol = symbol.upper()
        if symbol in ("XAUUSD", "OANDA:XAUUSD", "FOREXCOM:XAUUSD"):
            # Gold CFD bei IG (Demo)
            return "CS.D.GOLD.CFD.IP"
        # fallback: direkt zurückgeben (falls du mal EPIC direkt reinschreibst)
        return symbol

    # --------------------
    # Public API
    # --------------------
    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        currency: str = "EUR",
    ) -> Dict[str, Any]:
        """
        Market-Order mit optionalem SL und TP eröffnen.
        side: 'buy' oder 'sell'
        size: CFD-Größe (z.B. EUR pro Punkt)
        """
        epic = self._symbol_to_epic(symbol)
        direction = "BUY" if side.lower() == "buy" else "SELL"

        payload: Dict[str, Any] = {
            "epic": epic,
            "direction": direction,
            "size": size,
            "orderType": "MARKET",
            "expiry": "DFB",
            "forceOpen": True,
            "guaranteedStop": False,
            "currencyCode": currency,
        }

        if take_profit is not None:
            payload["limitLevel"] = float(take_profit)
        if stop_loss is not None:
            payload["stopLevel"] = float(stop_loss)

        headers = self._headers()
        resp = requests.post(
            f"{self.base_url}/positions/otc",
            headers=headers,
            json=payload,
        )

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"IG place order failed: {resp.status_code} {resp.text}"
            )

        return resp.json()

    def update_stop_loss(
        self,
        deal_id: str,
        new_stop: float,
    ) -> Dict[str, Any]:
        """
        Stop Loss einer bestehenden Position anpassen.
        (Für deinen gestuften Trailing-SL später.)
        """
        payload = {
            "stopLevel": float(new_stop),
        }

        headers = self._headers()
        resp = requests.put(
            f"{self.base_url}/positions/otc/{deal_id}",
            headers=headers,
            json=payload,
        )

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"IG update SL failed: {resp.status_code} {resp.text}"
            )

        return resp.json()