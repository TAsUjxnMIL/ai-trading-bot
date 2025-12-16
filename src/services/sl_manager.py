# src/services/sl_manager.py

import asyncio
from typing import Dict, Any, List, Optional, Set

from utils.logger import logger
from broker import broker_client
from services.trade_engine import compute_step_sl_long, compute_step_sl_short
from services.trade_repo import get_open_bot_deal_ids

POLL_SECONDS = 1.5          # how often to poll
MIN_SL_MOVE = 0.10          # only update if SL changes by at least this amount
ERROR_BACKOFF = 5.0         # backoff on errors


def _extract_position_fields(pos: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize an IG open position row (from trading-ig DataFrame -> dict)
    into our internal format.

    With pandas installed, trading-ig expands columns so records typically contain:
      - dealId
      - epic
      - direction ("BUY"/"SELL")
      - level (open level)
      - stopLevel (may be None)

    Some versions may still expose openLevel; we support both.
    """
    if not isinstance(pos, dict):
        return None

    deal_id = pos.get("dealId") or pos.get("deal_id")
    symbol = pos.get("epic") or pos.get("symbol")
    entry = pos.get("level") or pos.get("openLevel") or pos.get("entry_price")

    direction = (pos.get("direction") or "").upper()
    if direction == "BUY":
        side = "long"
    elif direction == "SELL":
        side = "short"
    else:
        side = pos.get("side")  # fallback if already normalized

    stop = pos.get("stopLevel") or pos.get("stop_loss") or pos.get("stop")

    if not deal_id or not symbol or entry is None or side not in ("long", "short"):
        return None

    return {
        "deal_id": str(deal_id),
        "symbol": str(symbol),  # epic
        "side": side,
        "entry_price": float(entry),
        "stop_loss": float(stop) if stop is not None else None,
    }


class SLManager:
    """
    Background task that monitors open positions and updates SL in steps.

    Safety:
      - Only manages positions whose deal_id exists in our DB table bot_trades (status=OPEN).
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
        logger.info("[SL_MANAGER] started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[SL_MANAGER] stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
                await asyncio.sleep(POLL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f"[SL_MANAGER] loop error: {e}")
                await asyncio.sleep(ERROR_BACKOFF)

    async def _tick(self) -> None:
        #logger.info("[SL_MANAGER] checking open positions for SL updates")

        # 1) Load bot-managed deals from DB
        managed_deals: Set[str] = get_open_bot_deal_ids()
        if not managed_deals:
            return

        # 2) Fetch open positions from broker
        positions = await broker_client.get_open_positions()
        if not positions:
            return

        # 3) Normalize + filter only bot-managed positions
        normalized: List[Dict[str, Any]] = []
        for p in positions:
            np = _extract_position_fields(p)
            if not np:
                continue
            if np["deal_id"] not in managed_deals:
                continue
            normalized.append(np)

        if not normalized:
            return

        # 4) Cache prices per symbol per tick (reduces API calls)
        price_cache: Dict[str, float] = {}

        for pos in normalized:
            deal_id = pos["deal_id"]
            symbol = pos["symbol"]      # epic
            side = pos["side"]
            entry = pos["entry_price"]

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

            # If no SL exists at broker and none cached, set it once
            if ref_sl is None:
                logger.info(
                    f"[SL_MANAGER] initial SL set deal={deal_id} {symbol} {side}: "
                    f"-> {new_sl:.2f} (px={current_price:.2f}, entry={entry:.2f})"
                )
                await broker_client.update_stop_loss(deal_id, new_sl)
                self._last_sl_by_deal[deal_id] = new_sl
                continue

            # Anti-spam threshold
            if abs(new_sl - ref_sl) < MIN_SL_MOVE:
                continue

            # Never worsen the stop
            if side == "long" and new_sl <= ref_sl:
                continue
            if side == "short" and new_sl >= ref_sl:
                continue

            logger.info(
                f"[SL_MANAGER] update SL deal={deal_id} {symbol} {side}: "
                f"{ref_sl:.2f} -> {new_sl:.2f} (px={current_price:.2f}, entry={entry:.2f})"
            )

            await broker_client.update_stop_loss(deal_id, new_sl)
            self._last_sl_by_deal[deal_id] = new_sl
