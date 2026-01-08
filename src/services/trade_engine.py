# src/services/trade_engine.py

from math import floor
from typing import Optional, Dict, Any, List
import uuid
import math

from utils.logger import logger
from broker import broker_client
from models.tradingview_signal import TradingviewSignal

from services.trade_repo import (
    add_open_trade,
    create_trade_group,
    has_active_trade_group,
    set_trade_group_status,
)

# ──────────────────────────────
# Konfiguration – XAUUSD-Dollar (nicht Pips)
# ──────────────────────────────

# 3-Take-Profit Ladder
TP_LEVELS_POINTS: List[float] = [3.0, 6.0, 9.0]

# Initialer Stop (fixer Abstand)
SL_INITIAL_POINTS: float = 10.0

# Step-Trailing Parameter (wird vom TradeLifeCycleManager genutzt)
SL_BASE_POINTS: float = 10.0
STEP_DIST_POINTS: float = 5.0
STEP_SIZE_POINTS: float = 1.0

# Gesamtgröße pro Signal (TradeGroup)
TOTAL_POSITION_SIZE: float = 0.6

# Size-Regeln für dein GOLD-Instrument laut Log:
# LIVE GOLD: minDealSize=0.125 (laut IG), sinnvolles step=0.025
SIZE_STEP: float = 0.025
MIN_DEAL_SIZE: float = 0.125
NUM_ORDERS: int = 3


# ──────────────────────────────
# Hilfsfunktionen: Step-SL Berechnung (für TradeLifeCycleManager)
# ──────────────────────────────

def compute_step_sl_long(
    entry_price: float,
    current_price: float,
    sl_base: float = SL_BASE_POINTS,
    step_dist: float = STEP_DIST_POINTS,
    step_size: float = STEP_SIZE_POINTS,
) -> float:
    """
    Long-Variante der gestuften SL-Logik wie in deinem Pine-Skript.

    distFromEntry = current - entry
    steps = max(floor(distFromEntry / stepDist), 0)
    SL = entry - sl_base + steps * step_size
    SL nie über aktuellen Kurs ziehen.
    """
    dist_from_entry = current_price - entry_price
    steps = max(floor(dist_from_entry / step_dist), 0)
    sl = entry_price - sl_base + steps * step_size
    sl = min(sl, current_price)
    return sl


def compute_step_sl_short(
    entry_price: float,
    current_price: float,
    sl_base: float = SL_BASE_POINTS,
    step_dist: float = STEP_DIST_POINTS,
    step_size: float = STEP_SIZE_POINTS,
) -> float:
    """
    Short-Variante der gestuften SL-Logik.

    distFromEntryShort = entry - current
    steps = max(floor(distFromEntryShort / stepDist), 0)
    SL = entry + sl_base - steps * step_size
    SL nie unter aktuellen Kurs ziehen.
    """
    dist_from_entry = entry_price - current_price
    steps = max(floor(dist_from_entry / step_dist), 0)
    sl = entry_price + sl_base - steps * step_size
    sl = max(sl, current_price)
    return sl


def _extract_deal_id(order_response: Any) -> Optional[str]:
    """
    Extract dealId from the trading-ig confirm response.

    Usually /confirms/{deal_reference} returns a dict containing 'dealId'.
    We still handle a few possible shapes defensively.
    """
    if not isinstance(order_response, dict):
        return None
    return (
        order_response.get("dealId")
        or order_response.get("deal_id")
        or (order_response.get("deal") or {}).get("dealId")
        or (order_response.get("position") or {}).get("dealId")
    )


def _extract_fill_level(order_response: Any) -> Optional[float]:
    if not isinstance(order_response, dict):
        return None
    lvl = order_response.get("level")
    try:
        return float(lvl) if lvl is not None else None
    except Exception:
        return None


def _round_down_to_step(x: float, step: float) -> float:
    return math.floor(x / step) * step


def split_total_size(total: float, n: int, step: float, min_size: float) -> List[float]:
    if n <= 0:
        raise ValueError("n must be > 0")
    if step <= 0:
        raise ValueError("step must be > 0")
    if total <= 0:
        raise ValueError("total must be > 0")

    # ✅ Ensure step is compatible with min_size (otherwise we can never hit min_size cleanly)
    q = min_size / step
    if abs(q - round(q)) > 1e-6:
        raise ValueError(f"min_size={min_size} is not a multiple of step={step}. Use a smaller step (e.g. 0.025)")

    min_total = n * min_size
    if total + 1e-9 < min_total:
        raise ValueError(
            f"TOTAL_POSITION_SIZE={total} is too small for {n} orders with minDealSize={min_size}. "
            f"Need at least {min_total}."
        )

    # ✅ Start with the minimum on each order (guarantees per-order min)
    sizes = [min_size] * n

    # remaining amount to distribute on top of min_size blocks
    remaining = round(total - sum(sizes), 10)

    # distribute in step increments round-robin
    i = 0
    while remaining >= (step - 1e-9):
        idx = i % n
        sizes[idx] = round(sizes[idx] + step, 10)
        remaining = round(total - sum(sizes), 10)
        i += 1
        if i > 100000:
            raise RuntimeError("split_total_size: too many iterations (unexpected).")

    # final validation
    sizes = [round(s, 10) for s in sizes]

    for s in sizes:
        if s + 1e-9 < min_size:
            raise RuntimeError(f"Computed size {s} < min_size {min_size}")
        q2 = s / step
        if abs(q2 - round(q2)) > 1e-6:
            raise RuntimeError(f"Computed size {s} is not a multiple of step {step}")

    if abs(sum(sizes) - total) > 1e-6:
        raise RuntimeError(f"Size split mismatch: sum(sizes)={sum(sizes)} != total={total}")

    return sizes


async def process_signal(tv_signal: TradingviewSignal, signal_id: int) -> None:
    """
    TradingView-Webhook → Bot-Logik → Order bei IG.

    Strategie:
    - TradingView sendet Entry (long/short + price)
    - Bot eröffnet Market Position mit TP/initial SL (Stufe 0)
    - dealId wird gespeichert, damit TradeLifeCycleManager NUR Bot-Trades trailt
    """
    logger.info(f"[TRADE_ENGINE] processing signal: {tv_signal}")

    symbol = tv_signal.symbol
    side_raw = tv_signal.side.lower()

    if side_raw not in ("long", "short"):
        logger.warning(f"[TRADE_ENGINE] Unknown side in signal: {side_raw}")
        return

    # ENTRY-BLOCKER: keine neue TradeGroup wenn OPEN/PARTIAL existiert
    if has_active_trade_group(symbol):
        logger.info(
            f"[TRADE_ENGINE] skipping signal for {symbol}: active trade group (OPEN/PARTIAL) exists"
        )
        return

    is_long = side_raw == "long"
    side = "buy" if is_long else "sell"

    # 1) aktuellen Bid/Offer holen
    try:
        bid, offer = await broker_client.get_bid_offer(symbol)
    except Exception as e:
        logger.exception(f"[TRADE_ENGINE] could not fetch bid/offer from broker: {e}")
        return

    # 2) Entry-Referenz (Long -> Offer, Short -> Bid)
    entry_ref = offer if is_long else bid

    # 3) Initialer SL als fixer Abstand (10$) – fürs ORDER-Placement erstmal aus entry_ref
    sl_price_for_order = (entry_ref - SL_INITIAL_POINTS) if is_long else (entry_ref + SL_INITIAL_POINTS)

    trade_group_id = str(uuid.uuid4())

    # Dynamische Sizes berechnen: Das macht nur Sinn, um die Verluste kleinzuhalten, aber das reduziert uns nicht unbedingt den Verlust relativ
    try:
        sizes = split_total_size(
            total=TOTAL_POSITION_SIZE,
            n=NUM_ORDERS,
            step=SIZE_STEP,
            min_size=MIN_DEAL_SIZE,
        )
    except Exception as e:
        logger.exception(f"[TRADE_ENGINE] could not split total size: {e}")
        return

    logger.info(
        f"[TRADE_ENGINE] group={trade_group_id} | TV price={tv_signal.price} | "
        f"IG bid={bid:.2f} offer={offer:.2f} | entry_ref={entry_ref:.2f} side={side_raw} "
        f"SL0={sl_price_for_order:.2f} total_size={TOTAL_POSITION_SIZE} sizes={sizes}"
    )

    # TradeGroup in DB anlegen
    try:
        create_trade_group(
            trade_group_id=trade_group_id,
            symbol=symbol,
            side=side_raw,
            timeframe=getattr(tv_signal, "timeframe", None),
            signal_id=signal_id,
        )
    except Exception as e:
        logger.exception(f"[TRADE_ENGINE] failed to create trade group: {e}")
        return

    successful_trades = 0

    for tp_index, (tp_points, size) in enumerate(zip(TP_LEVELS_POINTS, sizes), start=1):
        # Fürs ORDER-Placement: TP erstmal aus entry_ref
        tp_price_for_order = (entry_ref + tp_points) if is_long else (entry_ref - tp_points)

        logger.info(
            f"[TRADE_ENGINE] placing ladder order: group={trade_group_id} tp_index={tp_index} "
            f"side={side_raw} tp_points={tp_points:.2f} TP={tp_price_for_order:.2f} "
            f"SL={sl_price_for_order:.2f} size={size:.4f}"
        )

        try:
            # tp and sl wird als distance behandelt im ig client. Wir traden und erhalten den wahren Entry von IG.
            # tp und sl sind abhängig vom tatsächlichen Entry.
            order_response: Dict[str, Any] = await broker_client.place_market_order(
                symbol=symbol,
                side=side,
                size=size,
                take_profit=tp_price_for_order,
                stop_loss=sl_price_for_order,
            )
            logger.info(f"[TRADE_ENGINE] order response (tp_index={tp_index}): {order_response}")

            deal_id = _extract_deal_id(order_response)
            if not deal_id:
                raise RuntimeError(f"No dealId in IG order response: {order_response}")

            fill_level = _extract_fill_level(order_response)
            stored_entry = float(fill_level) if fill_level is not None else float(entry_ref)

            # FIX: DB-Level basierend auf tatsächlichem Entry speichern (nicht entry_ref)
            tp_price_db = (stored_entry + tp_points) if is_long else (stored_entry - tp_points)
            sl_price_db = (stored_entry - SL_INITIAL_POINTS) if is_long else (stored_entry + SL_INITIAL_POINTS)

            add_open_trade(
                deal_id=str(deal_id),
                trade_group_id=trade_group_id,
                tp_index=tp_index,
                symbol=symbol,
                side=side_raw,
                entry_price=float(stored_entry),
                tp_price=float(tp_price_db),
                initial_sl=float(sl_price_db),
            )
            successful_trades += 1

        except Exception as e:
            logger.exception(
                f"[TRADE_ENGINE] error while placing ladder order (tp_index={tp_index}, TP={tp_points}): {e}"
            )
            continue

    # Fail-Safe gegen Zombie-Gruppen
    if successful_trades == 0:
        logger.warning(
            f"[TRADE_ENGINE] no trades stored for group={trade_group_id} -> marking group CLOSED"
        )
        set_trade_group_status(trade_group_id, "CLOSED")