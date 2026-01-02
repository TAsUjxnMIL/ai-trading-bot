# src/broker/ig_client.py

import os
import time
import threading
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import requests

from trading_ig.rest import IGService, TokenInvalidException

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

        # Lock to protect session recreation across threads (asyncio.to_thread)
        self._session_lock = threading.Lock()

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
        Re-login and (optionally) re-select account.
        IMPORTANT: Do not swallow exceptions; caller logic depends on success/failure.
        """
        logger.warning("[IG] recreating session ...")
        self.ig.create_session()
        if self.acc_number:
            self.ig.switch_account(self.acc_number, default_account=False)
        logger.info("[IG] session recreated")

    def _ig_call(self, fn, *args, **kwargs):
        """
        Wrapper that refreshes IG session exactly once on TokenInvalidException and retries.
        Thread-safe via _session_lock (because we call this via asyncio.to_thread).
        """
        try:
            return fn(*args, **kwargs)
        except TokenInvalidException:
            logger.warning("[IG] Token invalid -> refresh and retry")
            with self._session_lock:
                self._recreate_session()
            return fn(*args, **kwargs)

    @staticmethod
    def _get_dict_or_attr(obj: Any, key: str, default=None):
        if obj is None:
            return default
        if hasattr(obj, "get"):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _compute_marketable_level(self, direction: str, bid: float, offer: float) -> float:
        """
        Compute a marketable LIMIT level that stays on the correct side even with small quote moves.
        For this Gold epic you were rounding to 0.1, so we keep tick=0.1.
        """
        tick = 0.1
        spread = max(0.0, offer - bid)

        # dynamic buffer: at least 0.5, else 3*spread + 1 tick
        buffer = max(0.5, spread * 3.0 + tick)

        if direction == "BUY":
            level = offer + buffer
            # safety: BUYZ MUST be >= current offer
            if level < offer:
                level = offer + buffer
        else:
            level = bid - buffer
            # safety: SELL MUST be <= current bid
            if level > bid:
                level = bid - buffer

        # snap to tick
        return round(level / tick) * tick

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
          - BUY  -> LIMIT at (offer + buffer)
          - SELL -> LIMIT at (bid - buffer)

        TP/SL are expected as absolute price levels from your trade engine.
        We convert them into limit_distance / stop_distance.
        """
        epic = self._symbol_to_epic(symbol)
        direction = "BUY" if side.lower() in ("buy", "long") else "SELL"

        # 1) Fetch market details (truth source)
        market = self._ig_call(self.ig.fetch_market_by_epic, epic)

        instrument = self._get_dict_or_attr(market, "instrument")
        snapshot = self._get_dict_or_attr(market, "snapshot")
        rules = self._get_dict_or_attr(market, "dealingRules")

        if instrument is None or snapshot is None:
            raise RuntimeError(f"IG market response missing instrument/snapshot for epic={epic}: {market}")

        expiry = (self._get_dict_or_attr(instrument, "expiry") or "-")
        status = self._get_dict_or_attr(snapshot, "marketStatus")

        bid = self._get_dict_or_attr(snapshot, "bid")
        offer = self._get_dict_or_attr(snapshot, "offer")

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
            mds = self._get_dict_or_attr(rules, "minDealSize")
            msd = self._get_dict_or_attr(rules, "minStepDistance")
            if isinstance(mds, dict):
                min_deal = mds.get("value")
            if isinstance(msd, dict):
                min_step_dist = msd.get("value")

        if min_deal is not None and size < float(min_deal):
            raise RuntimeError(f"size={size} is below minDealSize={min_deal} for epic={epic}")

        step = float(min_step_dist) if min_step_dist is not None else 0.0
        logger.info(
            f"[IG] epic={epic} expiry={expiry} status={status} bid={bid} offer={offer} "
            f"minDealSize={min_deal} minStepDistance={step}"
        )

        # entry_ref for distance calculations: BUY uses offer, SELL uses bid
        entry_ref = offer if direction == "BUY" else bid

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
            # 🔁 refresh quote each attempt -> prevents WRONG_SIDE_OF_MARKET due to drift
            m2 = self._ig_call(self.ig.fetch_market_by_epic, epic)
            snap2 = self._get_dict_or_attr(m2, "snapshot")
            bid2 = self._get_dict_or_attr(snap2, "bid")
            offer2 = self._get_dict_or_attr(snap2, "offer")
            status2 = self._get_dict_or_attr(snap2, "marketStatus")

            if status2 != "TRADEABLE":
                raise RuntimeError(f"Market not tradeable (attempt={attempt}): epic={epic} status={status2}")

            if bid2 is None or offer2 is None:
                raise RuntimeError(f"Missing bid/offer (attempt={attempt}) for epic={epic}. bid={bid2} offer={offer2}")

            bid2 = float(bid2)
            offer2 = float(offer2)
            order_level = self._compute_marketable_level(direction, bid2, offer2)

            logger.info(
                f"[IG] attempt={attempt} epic={epic} direction={direction} bid={bid2} offer={offer2} order_level={order_level}"
            )

            resp = self._ig_call(
                self.ig.create_open_position,
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

            # retry on these transient/quote-drift reasons
            if reason in ("MARKET_ROLLED", "LIMIT_ORDER_WRONG_SIDE_OF_MARKET") and attempt < max_retries:
                time.sleep(sleep_s)
                continue

            raise RuntimeError(f"IG order rejected: {resp}")

        raise RuntimeError(f"IG order failed after retries: {last_resp}")

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """
        Robust against sporadic RemoteDisconnected/Connection aborted.
        Strategy:
          1) Retry a few times with exponential backoff
          2) Token invalid handled by _ig_call()
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

        for attempt in range(1, max_retries + 1):
            try:
                pos = self._ig_call(self.ig.fetch_open_positions)
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

        raise RuntimeError(f"IG fetch_open_positions failed after retries. last_err={last_err}")

    def get_current_price(self, symbol: str) -> float:
        epic = self._symbol_to_epic(symbol)
        market = self._ig_call(self.ig.fetch_market_by_epic, epic)

        snapshot = self._get_dict_or_attr(market, "snapshot")
        if snapshot is None:
            raise RuntimeError(f"Market snapshot missing: {market}")

        status = self._get_dict_or_attr(snapshot, "marketStatus")
        bid = self._get_dict_or_attr(snapshot, "bid")
        offer = self._get_dict_or_attr(snapshot, "offer")

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
        return self._ig_call(
            self.ig.update_open_position,
            deal_id=deal_id,
            stop_level=float(new_stop),
            limit_level=float(take_profit) if take_profit is not None else None,
        )

    def get_bid_offer(self, symbol: str) -> Tuple[float, float]:
        epic = self._symbol_to_epic(symbol)
        market = self._ig_call(self.ig.fetch_market_by_epic, epic)

        snapshot = self._get_dict_or_attr(market, "snapshot") or {}
        status = self._get_dict_or_attr(snapshot, "marketStatus")
        bid = self._get_dict_or_attr(snapshot, "bid")
        offer = self._get_dict_or_attr(snapshot, "offer")

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
        resp = self._ig_call(
            self.ig.fetch_account_activity,
            from_date=from_date,
            to_date=to_date,
            detailed=detailed,
            page_size=page_size,
        )

        if resp is None:
            return []

        # DataFrame-like
        if hasattr(resp, "to_dict"):
            try:
                return resp.to_dict(orient="records")
            except TypeError:
                return list(resp.to_dict().values())

        if isinstance(resp, list):
            return [x for x in resp if isinstance(x, dict)]

        if isinstance(resp, dict):
            for key in ("activities", "activity", "data"):
                v = resp.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
            return []

        return []
