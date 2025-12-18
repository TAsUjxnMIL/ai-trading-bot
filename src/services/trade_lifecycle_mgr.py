# src/services/trade_lifecycle_mgr.py

import asyncio
from typing import Dict, Any, List, Optional, Set
import math

from utils.logger import logger
from broker import broker_client

from services.trade_engine import compute_step_sl_long, compute_step_sl_short
from services.trade_repo import (
    get_open_bot_deal_ids,
    mark_trades_closed,
    get_trade_group_ids_for_deals,
    recompute_trade_group_status,
)

POLL_SECONDS = 5
MIN_SL_MOVE = 0.10
ERROR_BACKOFF = 5.0


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


class TradeLifeCycleManager:
    """
    Background task that:
    1) Reconciles broker state vs DB (CLOSED detection)
    2) Updates trade_group status (OPEN / PARTIAL / CLOSED)
    3) Applies SL trailing logic to still-open positions
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_sl_by_deal: Dict[str, float] = {}

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

    async def _tick(self) -> None:
        # ──────────────────────────────
        # 1) Load OPEN bot-managed deals from DB
        # ──────────────────────────────
        managed_deals: Set[str] = get_open_bot_deal_ids()
        if not managed_deals:
            return

        # ──────────────────────────────
        # 2) Fetch open positions from broker (may be empty!)
        # ──────────────────────────────
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

        # ──────────────────────────────
        # 3) Reconcile: DB OPEN but broker CLOSED
        # ──────────────────────────────
        closed_ids = managed_deals - broker_open_ids
        if closed_ids:
            logger.info(
                f"[TradeLifeCycleManager] detected CLOSED deals: {sorted(closed_ids)}"
            )

            # 3a) update bot_trades
            mark_trades_closed(closed_ids)

            # 3b) update trade_groups
            affected_groups = get_trade_group_ids_for_deals(closed_ids)
            for gid in affected_groups:
                recompute_trade_group_status(gid)

            # remove cached SLs for closed trades
            for did in closed_ids:
                self._last_sl_by_deal.pop(did, None)

        # ──────────────────────────────
        # 4) SL logic ONLY for still-open, bot-managed positions
        # ──────────────────────────────
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
                price_cache[symbol] = await broker_client.get_current_price(symbol)
            current_price = price_cache[symbol]

            new_sl = (
                compute_step_sl_long(entry, current_price)
                if side == "long"
                else compute_step_sl_short(entry, current_price)
            )

            old_sl = pos["stop_loss"]
            last_set = self._last_sl_by_deal.get(deal_id)
            ref_sl = last_set if last_set is not None else old_sl

            # First SL ever
            if ref_sl is None:
                logger.info(
                    f"[TradeLifeCycleManager] initial SL set deal={deal_id} "
                    f"{symbol} {side}: -> {new_sl:.2f}"
                )
                await broker_client.update_stop_loss(deal_id, new_sl, take_profit)
                self._last_sl_by_deal[deal_id] = new_sl
                continue

            # Anti-spam
            if abs(new_sl - ref_sl) < MIN_SL_MOVE:
                continue

            # Never worsen SL
            if side == "long" and new_sl <= ref_sl:
                continue
            if side == "short" and new_sl >= ref_sl:
                continue

            logger.info(
                f"[TradeLifeCycleManager] update SL deal={deal_id} {symbol} {side}: "
                f"{ref_sl:.2f} -> {new_sl:.2f}"
            )

            await broker_client.update_stop_loss(deal_id, new_sl, take_profit)
            self._last_sl_by_deal[deal_id] = new_sl
