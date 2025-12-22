# src/services/trade_lifecycle_mgr.py

import asyncio
from typing import Dict, Any, List, Optional, Set, Tuple
import math
from datetime import datetime, timedelta, timezone
import os
from utils.logger import logger
from broker import broker_client
from broker.ig_client import MarketClosedError

from services.trade_engine import compute_step_sl_long, compute_step_sl_short
from services.trade_repo import (
    get_open_bot_deal_ids,
    mark_trades_closed,
    get_trade_group_ids_for_deals,
    recompute_trade_group_status,
    set_trade_group_status,
    get_stale_empty_trade_groups,
    get_trade_by_deal_id,
    get_open_trades_in_group,
    set_trade_close_meta,
    get_trade_in_group_by_tp_index
)

POLL_SECONDS = 5
MIN_SL_MOVE = 0.10
ERROR_BACKOFF = 5.0
GC_GRACE_SECONDS = 60

# How far back to scan activities (keep small; you can increase if IG delays close events)
ACTIVITY_LOOKBACK_MINUTES = 24 * 60  # 24h

# classification tolerance (XAUUSD-ish)
CLOSE_EPS = 0.15
BREAKEVEN_BUFFER = 0.20  # move SL to entry +/- buffer


def _opt_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _extract_position_fields(pos: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(pos, dict):
        return None

    deal_id = pos.get("dealId")
    symbol = pos.get("epic")
    entry = pos.get("level")

    direction = (pos.get("direction") or "").upper()
    if direction == "BUY":
        side = "long"
    elif direction == "SELL":
        side = "short"
    else:
        return None

    stop = _opt_float(pos.get("stopLevel"))
    limit_level = _opt_float(pos.get("limitLevel"))

    if not deal_id or not symbol or entry is None:
        return None

    return {
        "deal_id": str(deal_id),
        "symbol": str(symbol),
        "side": side,
        "entry_price": float(entry),
        "stop_loss": stop,
        "take_profit": limit_level,
    }


def _classify_close_reason(
    level: Optional[float],
    stop_level: Optional[float],
    limit_level: Optional[float],
) -> str:
    if level is None:
        return "UNKNOWN"

    candidates: List[Tuple[str, float]] = []
    if limit_level is not None:
        candidates.append(("TP", abs(level - limit_level)))
    if stop_level is not None:
        candidates.append(("SL", abs(level - stop_level)))

    if not candidates:
        # Wir kennen weder SL noch TP -> können nicht sinnvoll klassifizieren
        return "UNKNOWN"

    reason, dist = min(candidates, key=lambda x: x[1])

    if dist <= CLOSE_EPS:
        return reason

    # Close-Level vorhanden, aber weder TP noch SL -> sehr wahrscheinlich manuell geschlossen
    logger.info(
        f"[close_reason] MANUAL level={level} stop={stop_level} limit={limit_level} "
        f"dist_stop={abs(level-stop_level) if stop_level is not None else None} "
        f"dist_limit={abs(level-limit_level) if limit_level is not None else None}"
    )
    return "MANUAL"




def _extract_position_closed_event(a: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Supports both:
    (A) flat activity rows (your current logs)
    (B) nested details/actions format

    Returns:
      {
        "event_id": str,      # unique id of close event (activity dealId)
        "pos_deal_id": str,   # affectedDealId (join key to BotTrade.deal_id)
        "level": float|None,  # close level
        "stopLevel": float|None,
        "limitLevel": float|None,
        "date": str|None
      }
    """
    if not isinstance(a, dict):
        return None

    # ---------- Format A: flat ----------
    if a.get("actionType") == "POSITION_CLOSED":
        pos_deal_id = a.get("affectedDealId")
        if not pos_deal_id:
            return None
        event_id = a.get("dealId") or a.get("id") or ""
        if not event_id:
            return None

        return {
            "event_id": str(event_id),
            "pos_deal_id": str(pos_deal_id),
            "level": _opt_float(a.get("level")),
            "stopLevel": _opt_float(a.get("stopLevel")),
            "limitLevel": _opt_float(a.get("limitLevel")),
            "date": a.get("date"),
        }

    # ---------- Format B: nested ----------
    details = a.get("details") or {}
    actions = details.get("actions") or []
    if not actions:
        return None

    closed_action = None
    for x in actions:
        if isinstance(x, dict) and x.get("actionType") == "POSITION_CLOSED":
            closed_action = x
            break
    if not closed_action:
        return None

    pos_deal_id = closed_action.get("affectedDealId") or closed_action.get("dealId")
    if not pos_deal_id:
        return None

    event_id = a.get("dealId") or a.get("id") or ""
    if not event_id:
        return None

    return {
        "event_id": str(event_id),
        "pos_deal_id": str(pos_deal_id),
        "level": _opt_float(details.get("level")),
        "stopLevel": _opt_float(details.get("stopLevel")),
        "limitLevel": _opt_float(details.get("limitLevel")),
        "date": a.get("date"),
    }



class TradeLifeCycleManager:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_sl_by_deal: Dict[str, float] = {}

        # ✅ in-memory dedupe; later move to DB: dedupe: find and remove redundant processing of same close event
        self._processed_close_events: Set[str] = set()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[TradeLifeCycleManager] started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[TradeLifeCycleManager] stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"[TradeLifeCycleManager] loop error: {e}")
                await asyncio.sleep(ERROR_BACKOFF)

    async def _process_closed_deals_tp_hit(self, closed_ids: Set[str]) -> None:
        """
        For deals that just got detected as closed, look up the matching POSITION_CLOSED
        account activity event, classify TP vs SL, persist close metadata (price + reason),
        and if it's TP:
        - tp_index=1 -> move SL of remaining trades to BE (entry +/- buffer)
        - tp_index=2 -> move SL of remaining trades to TP1 price
        """
        if not closed_ids:
            return
        
        now_utc = datetime.now(timezone.utc)
        from_utc = now_utc - timedelta(minutes=ACTIVITY_LOOKBACK_MINUTES)

        activities = await broker_client.fetch_account_activity(
            from_date=from_utc,
            to_date=now_utc,
            detailed=True,
            page_size=500,
        )
        if not activities:
            return

        # index close events by position deal id
        closes_by_pos: Dict[str, Dict[str, Any]] = {}
        for a in activities:
            ev = _extract_position_closed_event(a)
            if not ev:
                continue
            if not ev.get("event_id"):
                continue
            if ev["event_id"] in self._processed_close_events:
                continue

            # keep the most recent event if multiple show up (rare, but safer)
            closes_by_pos[ev["pos_deal_id"]] = ev

        for pos_deal_id in closed_ids:
            ev = closes_by_pos.get(pos_deal_id)
            if not ev:
                continue

            level = ev.get("level")
            stop_level = ev.get("stopLevel")
            limit_level = ev.get("limitLevel")

            reason = _classify_close_reason(level, stop_level, limit_level)
            print(f"Classified close reason for deal={pos_deal_id} as {reason}")

            # mark event processed (avoid reprocessing)
            self._processed_close_events.add(ev["event_id"])

            # persist close meta (even if not TP1/TP2)
            try:
                set_trade_close_meta(
                    deal_id=pos_deal_id,
                    closed_price=level,
                    close_reason=reason,
                    closed_at=None,
                )
            except Exception as e:
                logger.exception(f"[TradeLifeCycleManager] set_trade_close_meta failed deal={pos_deal_id}: {e}")

            trade = get_trade_by_deal_id(pos_deal_id)
            if not trade:
                continue

            tp_idx = getattr(trade, "tp_index", None)
            group_id = getattr(trade, "trade_group_id", None)

            if not group_id or tp_idx is None:
                continue

            # only react when closed by TP
            if reason != "TP":
                logger.info(
                    f"[TradeLifeCycleManager] close event deal={pos_deal_id} tp_index={tp_idx} reason={reason} -> no SL ladder move"
                )
                continue

            # We only implement ladder steps TP1 and TP2 (TP3 has no remaining trades).
            if tp_idx not in (1, 2):
                logger.info(
                    f"[TradeLifeCycleManager] TP hit deal={pos_deal_id} tp_index={tp_idx} -> no further ladder action"
                )
                continue

            remaining = get_open_trades_in_group(group_id, exclude_deal_id=pos_deal_id)
            if not remaining:
                continue

            # -------------------------
            # Decide target SL level
            # -------------------------
            target_sl: Optional[float] = None

            if tp_idx == 1:
                # TP1 -> move remaining to breakeven (entry +/- buffer individually)
                logger.warning(
                    f"[TradeLifeCycleManager] TP1 hit (deal={pos_deal_id}) -> move SL to BE for "
                    f"{len(remaining)} remaining trades in group={group_id}"
                )

                for t in remaining:
                    did = t.deal_id
                    side = (t.side or "").lower()
                    entry = float(t.entry_price)
                    be_sl = entry + BREAKEVEN_BUFFER if side == "short" else entry - BREAKEVEN_BUFFER

                    take_profit = float(t.tp_price) if getattr(t, "tp_price", None) is not None else None
                    await broker_client.update_stop_loss(did, be_sl, take_profit)
                    self._last_sl_by_deal[did] = be_sl

                continue  # done

            if tp_idx == 2:
                # TP2 -> move remaining (usually TP3) SL to TP1 price
                tp1_trade = get_trade_in_group_by_tp_index(group_id, tp_index=1)
                if not tp1_trade or getattr(tp1_trade, "tp_price", None) is None:
                    logger.warning(
                        f"[TradeLifeCycleManager] TP2 hit (deal={pos_deal_id}) but could not find TP1 price "
                        f"for group={group_id} -> skip SL ladder"
                    )
                    continue

                tp1_price = float(tp1_trade.tp_price)
                logger.warning(
                    f"[TradeLifeCycleManager] TP2 hit (deal={pos_deal_id}) -> move SL to TP1 price={tp1_price:.2f} "
                    f"for {len(remaining)} remaining trades in group={group_id}"
                )

                for t in remaining:
                    did = t.deal_id
                    take_profit = float(t.tp_price) if getattr(t, "tp_price", None) is not None else None

                    await broker_client.update_stop_loss(did, tp1_price, take_profit)
                    self._last_sl_by_deal[did] = tp1_price


    async def _tick(self) -> None:
        # Garbage Collector (race-safe):
        # schließt NUR Gruppen, die >GC_GRACE_SECONDS alt sind UND komplett leer (0 Trades total)
        stale_empty = get_stale_empty_trade_groups(grace_seconds=GC_GRACE_SECONDS)
        for gid in stale_empty:
            logger.warning(f"[TradeLifeCycleManager] repairing stale empty group={gid} -> CLOSED")
            set_trade_group_status(gid, "CLOSED")

        # 1) Load OPEN bot-managed deals from DB
        managed_deals: Set[str] = get_open_bot_deal_ids()
        if not managed_deals:
            return

        # 2) Fetch open positions from broker
        positions = await broker_client.get_open_positions()

        broker_open_ids: Set[str] = set()
        normalized: List[Dict[str, Any]] = []

        if positions:
            for p in positions:
                np = _extract_position_fields(p)
                if not np:
                    continue
                broker_open_ids.add(np["deal_id"])
                if np["deal_id"] in managed_deals:
                    normalized.append(np)

        # 3) Reconcile: DB OPEN but broker CLOSED
        closed_ids = managed_deals - broker_open_ids
        if closed_ids:
            logger.info(f"[TradeLifeCycleManager] detected CLOSED deals: {sorted(closed_ids)}")

            # mark closed in DB
            mark_trades_closed(closed_ids)

            # NEW: now classify TP/SL and if TP1 hit, BE-move remaining
            await self._process_closed_deals_tp_hit(closed_ids)

            affected_groups = get_trade_group_ids_for_deals(closed_ids)
            for gid in affected_groups:
                recompute_trade_group_status(gid)

            for did in closed_ids:
                self._last_sl_by_deal.pop(did, None)

        # trailing SL logic stays as-is
        if not normalized:
            return

        price_cache: Dict[str, float] = {}

        for pos in normalized:
            deal_id = pos["deal_id"]
            symbol = pos["symbol"]
            side = pos["side"]
            entry = pos["entry_price"]
            take_profit = pos["take_profit"]

            if symbol not in price_cache:
                try:
                    price_cache[symbol] = await broker_client.get_current_price(symbol)
                except MarketClosedError as e:
                    # ✅ Minimal: skip trailing updates while market is closed
                    logger.info(f"[TradeLifeCycleManager] skip SL update (no quote/market closed) symbol={symbol}: {e}")
                    continue

            current_price = price_cache.get(symbol)
            if current_price is None:
                continue

            new_sl = (
                compute_step_sl_long(entry, current_price)
                if side == "long"
                else compute_step_sl_short(entry, current_price)
            )

            old_sl = pos["stop_loss"]
            last_set = self._last_sl_by_deal.get(deal_id)
            ref_sl = last_set if last_set is not None else old_sl

            if ref_sl is None:
                logger.info(
                    f"[TradeLifeCycleManager] initial SL set deal={deal_id} {symbol} {side}: -> {new_sl:.2f}"
                )
                await broker_client.update_stop_loss(deal_id, new_sl, take_profit)
                self._last_sl_by_deal[deal_id] = new_sl
                continue

            if abs(new_sl - ref_sl) < MIN_SL_MOVE:
                continue

            if side == "long" and new_sl <= ref_sl:
                continue
            if side == "short" and new_sl >= ref_sl:
                continue

            logger.info(
                f"[TradeLifeCycleManager] update SL deal={deal_id} {symbol} {side}: {ref_sl:.2f} -> {new_sl:.2f}"
            )

            await broker_client.update_stop_loss(deal_id, new_sl, take_profit)
            self._last_sl_by_deal[deal_id] = new_sl