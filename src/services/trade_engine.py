# src/services/trade_engine.py

from math import floor
from typing import Optional, Dict, Any

from utils.logger import logger
from broker import broker_client
from models.tradingview_signal import TradingviewSignal
from services.trade_repo import add_open_trade  # NEW


# ──────────────────────────────
# Konfiguration – analog zu deinem Pine-Strategy-Skript
# Werte in XAUUSD-Dollar (nicht in Pips)
# ──────────────────────────────

TP_POINTS: float        = 36.0   # Take Profit Abstand (z.B. +36$ über Entry bei Long)
SL_BASE_POINTS: float   = 3.5    # initialer SL-Abstand (z.B. -3.5$ unter Entry bei Long)
STEP_DIST_POINTS: float = 5.0    # alle +5$ Gewinn → eine "Stufe"
STEP_SIZE_POINTS: float = 1.0    # pro Stufe rückt SL um 1$ Richtung Entry

# CFD-Größe pro Trade (je nach Risiko / Konto anpassen)
POSITION_SIZE: float = 2       # z.B. 0.5 €/Punkt – bitte anpassen


# ──────────────────────────────
# Hilfsfunktionen: Step-SL Berechnung
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
    lvl = order_response.get("level") #or order_response.get("openLevel") or order_response.get("open_level")
    try:
        return float(lvl) if lvl is not None else None
    except Exception:
        return None


async def process_signal(tv_signal: TradingviewSignal) -> None:
    """
    TradingView-Webhook → Bot-Logik → Order bei IG.

    Strategie:
    - TradingView sendet Entry (long/short + price)
    - Bot eröffnet Market Position mit TP/initial SL (Stufe 0)
    - dealId wird gespeichert, damit SLManager NUR Bot-Trades trailt
    """
    logger.info(f"[TRADE_ENGINE] processing signal: {tv_signal}")

    symbol = tv_signal.symbol
    side_raw = tv_signal.side.lower()

    if side_raw not in ("long", "short"):
        logger.warning(f"[TRADE_ENGINE] Unknown side in signal: {side_raw}")
        return

    is_long = side_raw == "long"
    side = "buy" if is_long else "sell"

    try:
        bid, offer = await broker_client.get_bid_offer(symbol)
    except Exception as e:
        logger.exception(f"[TRADE_ENGINE] could not fetch bid/offer from broker: {e}")
        return

    entry_ref = offer if is_long else bid  # LONG -> offer, SHORT -> bid

    # TP & initialer SL (Stufe 0) — jetzt konsistent zur Broker-Preiswelt
    if is_long:
        tp_price = entry_ref + TP_POINTS
        sl_price = compute_step_sl_long(entry_ref, entry_ref)
    else:
        tp_price = entry_ref - TP_POINTS
        sl_price = compute_step_sl_short(entry_ref, entry_ref)

    logger.info(
        f"[TRADE_ENGINE] TV price={tv_signal.price} | IG bid={bid:.2f} offer={offer:.2f} | "
        f"entry_ref={entry_ref:.2f} side={side_raw} TP={tp_price:.2f} SL0={sl_price:.2f} size={POSITION_SIZE}"
    )

    try:
        order_response: Dict[str, Any] = await broker_client.place_market_order(
            symbol=symbol,
            side=side,
            size=POSITION_SIZE,
            take_profit=tp_price,
            stop_loss=sl_price,
        )
        logger.info(f"[TRADE_ENGINE] order response: {order_response}")

        deal_id = _extract_deal_id(order_response)
        if not deal_id:
            raise RuntimeError(f"No dealId in IG order response: {order_response}")

        fill_level = _extract_fill_level(order_response)
        stored_entry = fill_level if fill_level is not None else entry_ref

        # Persist bot-managed trade (store REAL broker entry if we have it)
        add_open_trade(
            deal_id=str(deal_id),
            symbol=symbol,
            side=side_raw,
            entry_price=float(stored_entry),
            tp_price=float(tp_price),
            initial_sl=float(sl_price),
        )

        logger.info(f"[TRADE_ENGINE] stored bot trade deal_id={deal_id} entry={stored_entry:.2f}")

    except Exception as e:
        logger.exception(f"[TRADE_ENGINE] error while placing order: {e}")
