# src/broker/ig_client.py
import os
from typing import Optional, Dict, Any, List

from trading_ig.rest import IGService


class IGClient:
    """
    IG client using the trading-ig wrapper (IGService).

    Env vars (recommended):
      IG_SERVICE_USERNAME
      IG_SERVICE_PASSWORD
      IG_SERVICE_API_KEY
      IG_SERVICE_ACC_TYPE   (DEMO or LIVE) - optional, default DEMO
      IG_SERVICE_ACC_NUMBER (optional)
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
        acc_type: Optional[str] = None,      # "DEMO" or "LIVE"
        acc_number: Optional[str] = None,    # account number/id
    ) -> None:
        self.username = username or os.getenv("IG_SERVICE_USERNAME") or os.getenv("IG_USERNAME")
        self.password = password or os.getenv("IG_SERVICE_PASSWORD") or os.getenv("IG_PASSWORD")
        self.api_key = api_key or os.getenv("IG_SERVICE_API_KEY") or os.getenv("IG_API_KEY")
        self.acc_type = (acc_type or os.getenv("IG_SERVICE_ACC_TYPE") or os.getenv("IG_ENV") or "DEMO").upper()
        self.acc_number = acc_number or os.getenv("IG_SERVICE_ACC_NUMBER")

        if not self.username or not self.password or not self.api_key:
            raise ValueError("Missing IG credentials (env or constructor args).")

        # Create service + login
        self.ig = IGService(self.username, self.password, self.api_key, self.acc_type)
        self.ig.create_session()

        # Optional: ensure correct active account
        if self.acc_number:
            self.ig.switch_account(self.acc_number, default_account=False)

    @staticmethod
    def _symbol_to_epic(symbol: str) -> str:
        symbol = symbol.upper()
        if symbol in ("XAUUSD", "OANDA:XAUUSD", "FOREXCOM:XAUUSD"):
            return "CS.D.GOLD.CFD.IP"
        return symbol

    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        currency: str = "EUR",
    ) -> Dict[str, Any]:
        epic = self._symbol_to_epic(symbol)
        direction = "BUY" if side.lower() in ("buy", "long") else "SELL"

        return self.ig.create_open_position(
            currency_code=currency,
            direction=direction,
            epic=epic,
            order_type="MARKET",
            expiry="DFB",
            force_open=True,
            guaranteed_stop=False,
            size=size,
            level=None,
            limit_level=float(take_profit) if take_profit is not None else None,
            stop_level=float(stop_loss) if stop_loss is not None else None,
            limit_distance=None,
            stop_distance=None,
            quote_id=None,
            trailing_stop=None,
            trailing_stop_increment=None,
        )

    def get_open_positions(self) -> List[Dict[str, Any]]:
        pos = self.ig.fetch_open_positions()

        # trading-ig often returns a pandas DataFrame
        if hasattr(pos, "to_dict"):
            return pos.to_dict(orient="records")

        if isinstance(pos, list):
            return pos

        return [pos] if isinstance(pos, dict) else []

    def get_current_price(self, symbol: str) -> float:
        epic = self._symbol_to_epic(symbol)
        market = self.ig.fetch_market_by_epic(epic)

        # market can be dict-like or object-like depending on wrapper version
        snapshot = market.get("snapshot") if hasattr(market, "get") else getattr(market, "snapshot", None)
        if snapshot is None:
            raise RuntimeError(f"Market snapshot missing: {market}")

        bid = snapshot.get("bid") if hasattr(snapshot, "get") else getattr(snapshot, "bid", None)
        offer = snapshot.get("offer") if hasattr(snapshot, "get") else getattr(snapshot, "offer", None)

        if bid is None and offer is None:
            raise RuntimeError(f"Missing bid/offer in snapshot: {snapshot}")

        if bid is None:
            return float(offer)
        if offer is None:
            return float(bid)
        return (float(bid) + float(offer)) / 2.0

    def update_stop_loss(self, deal_id: str, new_stop: float) -> Dict[str, Any]:
    # IGService has update_open_position in your installation
        return self.ig.update_open_position(
            deal_id=deal_id,
            stop_level=float(new_stop),
            # optional extras if you ever need them:
            # limit_level=None,
            # trailing_stop=None,
            # trailing_stop_increment=None,
        )
