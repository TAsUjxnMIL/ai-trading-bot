# src/broker/ig_client.py

import os
import time
import threading
import math
from typing import Optional, Dict, Any, List, Tuple, Callable
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
        acc_type: Optional[str] = None,
        acc_number: Optional[str] = None,
    ) -> None:
        self.username = username or os.getenv("IG_SERVICE_USERNAME") or os.getenv("IG_USERNAME")
        self.password = password or os.getenv("IG_SERVICE_PASSWORD") or os.getenv("IG_PASSWORD")
        self.api_key = api_key or os.getenv("IG_SERVICE_API_KEY") or os.getenv("IG_API_KEY")

        raw_acc_type = (acc_type or os.getenv("IG_SERVICE_ACC_TYPE") or os.getenv("IG_ENV") or "DEMO").upper()
        if raw_acc_type in ("PRACTICE", "DEMO"):
            self.acc_type = "DEMO"
        elif raw_acc_type in ("LIVE", "REAL"):
            self.acc_type = "LIVE"
        else:
            self.acc_type = raw_acc_type

        self.acc_number = acc_number or os.getenv("IG_SERVICE_ACC_NUMBER")

        if not self.username or not self.password or not self.api_key:
            raise ValueError("Missing IG credentials (env or constructor args).")

        self._session_lock = threading.Lock()

        self.ig = self._new_service()
        self._create_session_initial()

    # ----------------------------
    # Internal: service/session
    # ----------------------------
    def _new_service(self) -> IGService:
        return IGService(self.username, self.password, self.api_key, self.acc_type)

    def _create_session_initial(self) -> None:
        logger.info(
            f"[IG][INIT] create_session acc_type={self.acc_type} "
            f"acc_number={'set' if self.acc_number else 'none'}"
        )
        self.ig.create_session()
        if self.acc_number:
            self.ig.switch_account(self.acc_number, default_account=False)
        logger.info("[IG][INIT] session ready")

    def _recreate_session_hard(self) -> None:
        logger.warning("[IG][SESSION] hard recreate session ...")
        self.ig = self._new_service()
        self.ig.create_session()
        if self.acc_number:
            self.ig.switch_account(self.acc_number, default_account=False)
        logger.info("[IG][SESSION] hard recreate done")

    @staticmethod
    def _looks_like_client_token_invalid(exc: Exception) -> bool:
        msg = str(exc).lower()
        return ("client-token-invalid" in msg) or ("error.security.client-token-invalid" in msg)

    def _ig_call(self, fn: Callable, *args, **kwargs):
        call_name = getattr(fn, "__name__", str(fn))
        try:
            return fn(*args, **kwargs)

        except TokenInvalidException as e:
            logger.warning(f"[IG][CALL] TokenInvalidException in {call_name}: {e} -> refresh+retry")
            with self._session_lock:
                self._recreate_session_hard()
            return fn(*args, **kwargs)

        except Exception as e:
            if self._looks_like_client_token_invalid(e):
                logger.warning(f"[IG][CALL] token invalid (generic) in {call_name}: {e} -> refresh+retry")
                with self._session_lock:
                    self._recreate_session_hard()
                return fn(*args, **kwargs)
            raise

    # ----------------------------
    # Helpers
    # ----------------------------
    @staticmethod
    def _symbol_to_epic(symbol: str) -> str:
        s = symbol.upper()
        if s in ("XAUUSD", "OANDA:XAUUSD", "FOREXCOM:XAUUSD", "FXCM:XAUUSD"):
            return "CS.D.CFEGOLD.CEA.IP"
        return symbol

    @staticmethod
    def _get_dict_or_attr(obj: Any, key: str, default=None):
        if obj is None:
            return default
        if hasattr(obj, "get"):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _round_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return round(value / step) * step

    @staticmethod
    def _round_down_tick(x: float, tick: float) -> float:
        return math.floor(x / tick) * tick

    @staticmethod
    def _round_up_tick(x: float, tick: float) -> float:
        return math.ceil(x / tick) * tick

    def _compute_marketable_level(self, direction: str, bid: float, offer: float) -> float:
        tick = 0.1
        spread = max(0.0, offer - bid)
        buffer = max(0.5, spread * 3.0 + tick)

        if direction == "BUY":
            level = offer + buffer
            # safety
            if level < offer:
                level = offer + buffer
            # marketable buy must be >= offer
            level = max(level, offer + tick)
            return self._round_up_tick(level, tick)

        level = bid - buffer
        if level > bid:
            level = bid - buffer
        # marketable sell must be <= bid
        level = min(level, bid - tick)
        return self._round_down_tick(level, tick)

    def _get_market_snapshot_and_rules(self, epic: str) -> Tuple[float, float, float, str]:
        m = self._ig_call(self.ig.fetch_market_by_epic, epic)
        snap = self._get_dict_or_attr(m, "snapshot") or {}
        rules = self._get_dict_or_attr(m, "dealingRules") or {}

        status = self._get_dict_or_attr(snap, "marketStatus")
        bid = self._get_dict_or_attr(snap, "bid")
        offer = self._get_dict_or_attr(snap, "offer")

        if status != "TRADEABLE" or bid is None or offer is None:
            raise MarketClosedError(f"No tradable bid/offer: epic={epic} status={status} bid={bid} offer={offer}")

        bid = float(bid)
        offer = float(offer)

        msd = self._get_dict_or_attr(rules, "minStepDistance")
        step = float(msd.get("value")) if isinstance(msd, dict) and msd.get("value") is not None else 0.0
        return bid, offer, step, str(status)

    def _clamp_stop_level(self, epic: str, direction: str, proposed_stop: float) -> float:
        """
        direction is POSITION direction: BUY (long) or SELL (short)
        Long stop must be <= bid - step
        Short stop must be >= offer + step
        """
        bid, offer, step, _ = self._get_market_snapshot_and_rules(epic)
        tick = 0.1
        stop = float(proposed_stop)

        if direction == "BUY":
            max_stop = bid - step
            if stop > max_stop:
                logger.warning(f"[IG][CLAMP_SL] BUY stop too high. proposed={stop} -> {max_stop} (bid={bid}, step={step})")
                stop = max_stop
            # round DOWN so we never cross above max_stop due to rounding
            stop = self._round_down_tick(stop, tick)
            # guard after rounding
            if stop > max_stop:
                stop = self._round_down_tick(max_stop, tick)

        else:  # SELL
            min_stop = offer + step
            if stop < min_stop:
                logger.warning(f"[IG][CLAMP_SL] SELL stop too low. proposed={stop} -> {min_stop} (offer={offer}, step={step})")
                stop = min_stop
            # round UP so we never cross below min_stop due to rounding
            stop = self._round_up_tick(stop, tick)
            # guard after rounding
            if stop < min_stop:
                stop = self._round_up_tick(min_stop, tick)

        return float(round(stop, 2))

    # ----------------------------
    # Orders
    # ----------------------------
    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        currency: str = "USD",
    ) -> Dict[str, Any]:
        epic = self._symbol_to_epic(symbol)
        direction = "BUY" if side.lower() in ("buy", "long") else "SELL"

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

        logger.info(f"[IG][ORDER] epic={epic} dir={direction} status={status} bid={bid} offer={offer} expiry={expiry}")

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

        entry_ref = offer if direction == "BUY" else bid
        stop_distance = None
        limit_distance = None

        if stop_loss is not None:
            sl = float(stop_loss)
            stop_distance = (entry_ref - sl) if direction == "BUY" else (sl - entry_ref)
            if stop_distance <= 0:
                raise RuntimeError(f"Invalid stop_loss for {direction}: stop_loss={sl} entry_ref={entry_ref}")
            if step:
                stop_distance = self._round_to_step(stop_distance, step)

        if take_profit is not None:
            tp = float(take_profit)
            limit_distance = (tp - entry_ref) if direction == "BUY" else (entry_ref - tp)
            if limit_distance <= 0:
                raise RuntimeError(f"Invalid take_profit for {direction}: take_profit={tp} entry_ref={entry_ref}")
            if step:
                limit_distance = self._round_to_step(limit_distance, step)

        max_retries = 5
        sleep_s = 0.6
        last_resp: Optional[Dict[str, Any]] = None

        for attempt in range(1, max_retries + 1):
            m2 = self._ig_call(self.ig.fetch_market_by_epic, epic)
            snap2 = self._get_dict_or_attr(m2, "snapshot") or {}
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
                f"[IG][ORDER] attempt={attempt}/{max_retries} epic={epic} dir={direction} "
                f"bid={bid2} offer={offer2} level={order_level} stop_dist={stop_distance} limit_dist={limit_distance} size={size}"
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
                level=order_level,
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

            last_resp = resp if isinstance(resp, dict) else {"resp": str(resp)}
            deal_status = last_resp.get("dealStatus")
            reason = last_resp.get("reason")

            logger.warning(f"[IG][ORDER] response attempt={attempt}: dealStatus={deal_status} reason={reason} resp={last_resp}")

            if deal_status == "ACCEPTED":
                return last_resp

            if reason in ("MARKET_ROLLED", "LIMIT_ORDER_WRONG_SIDE_OF_MARKET") and attempt < max_retries:
                time.sleep(sleep_s)
                continue

            raise RuntimeError(f"IG order rejected: {last_resp}")

        raise RuntimeError(f"IG order failed after retries: {last_resp}")

    # ----------------------------
    # Positions / Quotes
    # ----------------------------
    def get_open_positions(self) -> List[Dict[str, Any]]:
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
                out = _normalize(pos)
                logger.debug(f"[IG][OPEN_POS] attempt={attempt} got={len(out)}")
                return out

            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_err = e
                sleep = base_sleep * (2 ** (attempt - 1))
                logger.warning(f"[IG][OPEN_POS] transient error (attempt {attempt}/{max_retries}): {e} -> sleep {sleep:.2f}s")
                time.sleep(sleep)

            except Exception as e:
                if _is_disconnect_exc(e):
                    last_err = e
                    sleep = base_sleep * (2 ** (attempt - 1))
                    logger.warning(f"[IG][OPEN_POS] disconnect (attempt {attempt}/{max_retries}): {e} -> sleep {sleep:.2f}s")
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

        if status != "TRADEABLE" or (bid is None and offer is None):
            raise MarketClosedError(f"Missing bid/offer or not tradeable: epic={epic} status={status} bid={bid} offer={offer}")

        if bid is None:
            return float(offer)
        if offer is None:
            return float(bid)
        return (float(bid) + float(offer)) / 2.0

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

    # ----------------------------
    # Stop update (SAFE: keep TP unchanged)
    # ----------------------------
    def update_stop_loss(
        self,
        deal_id: str,
        new_stop: float,
        take_profit: Optional[float] = None,  # accepted for API compatibility, but NOT used to avoid TP changes
        clamp: bool = True,
    ) -> Dict[str, Any]:
        """
        IMPORTANT:
        - trading-ig (your installed version) requires BOTH stop_level and limit_level
        - To avoid 'invalid.request.limitLevel', we NEVER send TP from DB/strategy.
          We always keep the CURRENT TP from the live IG position.
        """
        # 1) find current position in IG open positions
        pos_list = self.get_open_positions()
        pos = None
        for p in pos_list:
            if isinstance(p, dict) and str(p.get("dealId")) == str(deal_id):
                pos = p
                break

        if not pos:
            raise RuntimeError(f"[IG][UPDATE_SL] deal_id={deal_id} not found in open positions")

        epic = str(pos.get("epic"))
        direction = str(pos.get("direction") or "").upper()  # BUY / SELL
        current_limit = pos.get("limitLevel")

        if current_limit is None:
            # If you truly have no TP on that position, your trading-ig version can't update SL safely.
            raise RuntimeError(
                f"[IG][UPDATE_SL] Position has no limitLevel (TP). "
                f"Cannot call update_open_position because this trading-ig version requires limit_level. deal_id={deal_id}"
            )

        tick = 0.1

        stop_level = float(new_stop)

        # clamp (uses market snapshot + minStepDistance)
        if clamp and epic and direction in ("BUY", "SELL"):
            try:
                stop_level = self._clamp_stop_level(epic, direction, stop_level)
            except MarketClosedError as e:
                logger.warning(f"[IG][UPDATE_SL] clamp skipped (market closed): {e}")
                # still do a safe rounding below

        # round stop safely:
        # - BUY => round DOWN (keep stop below market constraints)
        # - SELL => round UP
        if direction == "BUY":
            stop_level = self._round_down_tick(stop_level, tick)
        else:
            stop_level = self._round_up_tick(stop_level, tick)

        # limit_level: KEEP EXISTING TP exactly (just tick-normalize to be safe)
        limit_level = float(current_limit)
        limit_level = round(limit_level / tick) * tick

        logger.warning(
            f"[IG][UPDATE_SL] deal_id={deal_id} epic={epic} dir={direction} "
            f"stop_level={stop_level} limit_level(keep)={limit_level} "
            f"(raw_stop={new_stop} take_profit_ignored={take_profit} pos_tp={current_limit})"
        )

        # 2) call IG (positional because your wrapper signature requires limit_level param)
        resp = self._ig_call(
            self.ig.update_open_position,
            deal_id,
            float(stop_level),
            float(limit_level),
        )

        logger.warning(f"[IG][UPDATE_SL] resp deal_id={deal_id}: {resp}")
        return resp

    # ----------------------------
    # Activity
    # ----------------------------
    def fetch_account_activity(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        detailed: bool = True,
        page_size: int = 500,
    ) -> List[Dict[str, Any]]:
        resp = self._ig_call(
            self.ig.fetch_account_activity,
            from_date=from_date,
            to_date=to_date,
            detailed=detailed,
            page_size=page_size,
        )

        if resp is None:
            return []

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
