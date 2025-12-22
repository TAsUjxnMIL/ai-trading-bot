# src/broker/ig_client.py

import os
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import requests
from trading_ig.rest import IGService

from utils.logger import logger


class MarketClosedError(RuntimeError):
    """Raised when IG returns a market snapshot without tradable bid/offer (e.g., marketStatus=CLOSED)."""
    pass

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

        raw_acc_type = (acc_type or os.getenv("IG_SERVICE_ACC_TYPE") or os.getenv("IG_ENV") or "DEMO").upper()
        # normalize common values
        if raw_acc_type in ("PRACTICE", "DEMO"):
            self.acc_type = "DEMO"
        elif raw_acc_type in ("LIVE", "REAL"):
            self.acc_type = "LIVE"
        else:
            self.acc_type = raw_acc_type

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
        if symbol in ("XAUUSD", "OANDA:XAUUSD", "FOREXCOM:XAUUSD", "FXCM:XAUUSD"):
            return "CS.D.CFEGOLD.CEA.IP"
        return symbol

    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return round(value / step) * step

    def _recreate_session(self) -> None:
        """
        Best-effort re-login. Helps if IG drops the connection or session gets weird.
        """
        try:
            logger.warning("[IG] recreating session ...")
            self.ig.create_session()
            if self.acc_number:
                self.ig.switch_account(self.acc_number, default_account=False)
            logger.info("[IG] session recreated")
        except Exception as e:
            logger.exception(f"[IG] failed to recreate session: {e}")

    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        currency: str = "USD",
    ) -> Dict[str, Any]:
        """
        Despite the name, we DO NOT use IG 'MARKET' orders (too often rejected with MARKET_ROLLED).
        We place a 'marketable LIMIT' with FILL_OR_KILL:
          - BUY  -> LIMIT at (offer + small buffer)
          - SELL -> LIMIT at (bid - small buffer)

        TP/SL are expected as absolute price levels from your trade engine.
        We convert them into limit_distance / stop_distance (because your trading-ig signature requires them).
        """
        epic = self._symbol_to_epic(symbol)
        direction = "BUY" if side.lower() in ("buy", "long") else "SELL"

        # 1) Fetch market details (truth source)
        market = self.ig.fetch_market_by_epic(epic)

        # IG returns either dict or object with attributes depending on version
        # 1.) market is dict-like and contains the method get as an attribute
        # 2.) market is an object with attributes (e.g. instrument, snapshot, dealingRules)
        instrument = market.get("instrument") if hasattr(market, "get") else getattr(market, "instrument", None)
        snapshot = market.get("snapshot") if hasattr(market, "get") else getattr(market, "snapshot", None)
        rules = market.get("dealingRules") if hasattr(market, "get") else getattr(market, "dealingRules", None)

        if instrument is None or snapshot is None:
            raise RuntimeError(f"IG market response missing instrument/snapshot for epic={epic}: {market}")

        expiry = (instrument.get("expiry") if hasattr(instrument, "get") else getattr(instrument, "expiry", None)) or "-"
        status = (snapshot.get("marketStatus") if hasattr(snapshot, "get") else getattr(snapshot, "marketStatus", None))

        # Goldhändler sagt ich kaufe zu dem Preis von Bid: Also wenn ich verkaufe, bekomm ich den Preis
        bid = snapshot.get("bid") if hasattr(snapshot, "get") else getattr(snapshot, "bid", None)
        # Goldhändler sagt ich verkaufe zu dem Preis von Offer: Also wenn ich kaufe, bezahle ich den Preis
        offer = snapshot.get("offer") if hasattr(snapshot, "get") else getattr(snapshot, "offer", None)

        logger.info(f"[IG] epic={epic} ... bid={bid} offer={offer} ...")

        if status != "TRADEABLE":
            raise RuntimeError(f"Market not tradeable: epic={epic} status={status}")

        if bid is None or offer is None:
            raise RuntimeError(f"Missing bid/offer for epic={epic}. bid={bid} offer={offer}")

        bid = float(bid)
        offer = float(offer)

        min_deal = None
        min_step_dist = None
        if rules is not None:
            min_deal = (rules.get("minDealSize") or {}).get("value") if hasattr(rules, "get") else None
            min_step_dist = (rules.get("minStepDistance") or {}).get("value") if hasattr(rules, "get") else None

        if min_deal is not None and size < float(min_deal):
            raise RuntimeError(f"size={size} is below minDealSize={min_deal} for epic={epic}")

        step = float(min_step_dist) if min_step_dist is not None else 0.0
        logger.info(
            f"[IG] epic={epic} expiry={expiry} status={status} bid={bid} offer={offer} "
            f"minDealSize={min_deal} minStepDistance={step}"
        )

        # Explanaton of LIMIT, FILL_OR_KILL, marketable limit:
        # LIMIT ORDER: 
        #   BUY LIMIT: order will only execute at limit price or lower -> Ich kaufe nicht teurer als den Limitpreis
        #   SELL LIMIT: order will only execute at limit price or higher -> Ich verkaufe nicht billiger als den Limitpreis
        # FILL_OR_KILL: either fully executed immediately at the limit price (or better) or cancelled
        # Marketable LIMIT:
        #   BUY: set limit slightly above current offer -> sofortige Ausführung
        #   SELL: set limit slightly below current bid -> sofortige Ausführung
        # This is what we do with order_level below.
        tick = 0.1
        buffer_ticks = 5  # 0.5
        if direction == "BUY":
            order_level = round(offer + buffer_ticks * tick, 1)
            entry_ref = offer
        else:
            order_level = round(bid - buffer_ticks * tick, 1)
            entry_ref = bid

        stop_distance = None
        limit_distance = None

        if stop_loss is not None:
            sl = float(stop_loss)
            if direction == "BUY":
                stop_distance = entry_ref - sl
            else:
                stop_distance = sl - entry_ref
            if stop_distance <= 0:
                raise RuntimeError(f"Invalid stop_loss for {direction}: stop_loss={sl} entry_ref={entry_ref}")
            if step:
                stop_distance = self._round_to_step(stop_distance, step)

        if take_profit is not None:
            tp = float(take_profit)
            if direction == "BUY":
                limit_distance = tp - entry_ref
            else:
                limit_distance = entry_ref - tp
            if limit_distance <= 0:
                raise RuntimeError(f"Invalid take_profit for {direction}: take_profit={tp} entry_ref={entry_ref}")
            if step:
                limit_distance = self._round_to_step(limit_distance, step)

        max_retries = 5
        sleep_s = 0.6
        last_resp: Optional[Dict[str, Any]] = None

        for attempt in range(1, max_retries + 1):
            resp = self.ig.create_open_position(
                currency_code=currency,
                direction=direction,
                epic=epic,
                expiry=expiry,
                force_open=True,
                guaranteed_stop=False,
                order_type="LIMIT",
                level=order_level,  # marketable limit
                time_in_force="FILL_OR_KILL",
                size=size,

                stop_distance=stop_distance,
                limit_distance=limit_distance,

                stop_level=None,
                limit_level=None,
                quote_id=None,
                trailing_stop=False,
                trailing_stop_increment=None,
                session=None,
            )

            last_resp = resp
            deal_status = resp.get("dealStatus") if isinstance(resp, dict) else None
            reason = resp.get("reason") if isinstance(resp, dict) else None

            if deal_status == "ACCEPTED":
                return resp

            if reason == "MARKET_ROLLED" and attempt < max_retries:
                time.sleep(sleep_s)
                continue

            raise RuntimeError(f"IG order rejected: {resp}")

        raise RuntimeError(f"IG order failed after retries: {last_resp}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Robust against sporadic RemoteDisconnected/Connection aborted.
        Strategy:
          1) Retry a few times with exponential backoff (no re-login)
          2) If still failing: recreate session once + do one final attempt
        """
        max_retries = 5
        base_sleep = 0.5

        def _normalize(pos: Any) -> List[Dict[str, Any]]:
            if hasattr(pos, "to_dict"):
                return pos.to_dict(orient="records")
            if isinstance(pos, list):
                return pos
            return [pos] if isinstance(pos, dict) else []

        def _is_disconnect_exc(e: Exception) -> bool:
            msg = str(e)
            return ("RemoteDisconnected" in msg) or ("Connection aborted" in msg)

        last_err: Optional[Exception] = None

        # 1) Normal retries (no session recreation)
        for attempt in range(1, max_retries + 1):
            try:
                pos = self.ig.fetch_open_positions()
                return _normalize(pos)

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_err = e
                sleep = base_sleep * (2 ** (attempt - 1))
                logger.warning(
                    f"[IG] fetch_open_positions transient error (attempt {attempt}/{max_retries}): {e} -> sleep {sleep:.2f}s"
                )
                time.sleep(sleep)

            except Exception as e:
                if _is_disconnect_exc(e):
                    last_err = e
                    sleep = base_sleep * (2 ** (attempt - 1))
                    logger.warning(
                        f"[IG] fetch_open_positions disconnect (attempt {attempt}/{max_retries}): {e} -> sleep {sleep:.2f}s"
                    )
                    time.sleep(sleep)
                    continue
                raise

        # 2) If still failing: recreate session once + final attempt
        logger.warning(f"[IG] fetch_open_positions still failing after retries -> recreating session once. last_err={last_err}")
        self._recreate_session()

        try:
            pos = self.ig.fetch_open_positions()
            return _normalize(pos)
        except Exception as e:
            raise RuntimeError(f"IG fetch_open_positions failed after retries + session recreate: {e}") from e

    def get_current_price(self, symbol: str) -> float:
        epic = self._symbol_to_epic(symbol)
        market = self.ig.fetch_market_by_epic(epic)

        snapshot = market.get("snapshot") if hasattr(market, "get") else getattr(market, "snapshot", None)
        if snapshot is None:
            raise RuntimeError(f"Market snapshot missing: {market}")

        status = snapshot.get("marketStatus") if hasattr(snapshot, "get") else getattr(snapshot, "marketStatus", None)
        bid = snapshot.get("bid") if hasattr(snapshot, "get") else getattr(snapshot, "bid", None)
        offer = snapshot.get("offer") if hasattr(snapshot, "get") else getattr(snapshot, "offer", None)

        # Minimal but robust handling for CLOSED / not quoted markets
        if status != "TRADEABLE" or (bid is None and offer is None):
            raise MarketClosedError(
                f"Missing bid/offer or not tradeable: epic={epic} status={status} bid={bid} offer={offer}"
            )

        if bid is None:
            return float(offer)
        if offer is None:
            return float(bid)
        return (float(bid) + float(offer)) / 2.0

    def update_stop_loss(self, deal_id: str, new_stop: float, take_profit: Optional[float] = None) -> Dict[str, Any]:
        return self.ig.update_open_position(
            deal_id=deal_id,
            stop_level=float(new_stop),
            limit_level=float(take_profit) if take_profit is not None else None,
        )

    def get_bid_offer(self, symbol: str) -> tuple[float, float]:
        epic = self._symbol_to_epic(symbol)
        market = self.ig.fetch_market_by_epic(epic)
        snapshot = market.get("snapshot", {}) or {}

        status = snapshot.get("marketStatus")
        bid = snapshot.get("bid")
        offer = snapshot.get("offer")

        if status != "TRADEABLE" or bid is None or offer is None:
            raise MarketClosedError(f"No tradable bid/offer: epic={epic} status={status} bid={bid} offer={offer}")

        return float(bid), float(offer)
    

    def fetch_account_activity(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        detailed: bool = True,
        page_size: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Returns raw account activity entries as List[Dict[str, Any]].

        Note: trading-ig may return a DataFrame-like object; we normalize to list-of-dicts.
        """

        # trading-ig signature may differ slightly; the common one is:
        # fetch_account_activity_by_date(from_date, to_date, detailed=False, page_size=20, ...)
        resp = self.ig.fetch_account_activity(
            from_date=from_date,
            to_date=to_date,
            detailed=detailed,
            page_size=page_size,
        )

        # Normalize to list[dict]
        if resp is None:
            return []

        # DataFrame-like
        if hasattr(resp, "to_dict"):
            try:
                return resp.to_dict(orient="records")
            except TypeError:
                # some pandas versions don't support orient kw the same way
                return list(resp.to_dict().values())

        # Already list of dicts
        if isinstance(resp, list):
            return [x for x in resp if isinstance(x, dict)]

        # Some versions return {"activities": [...]} or similar
        if isinstance(resp, dict):
            for key in ("activities", "activity", "data"):
                v = resp.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
            # fallback: single dict entry
            return []
        return []
