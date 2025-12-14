# src/services/trade_engine.py

from math import floor
from typing import Optional

from utils.logger import logger
from broker import broker_client
from models.tradingview_signal import TradingviewSignal

# ──────────────────────────────
# Konfiguration – analog zu deinem Pine-Strategy-Skript
# Werte in XAUUSD-Dollar (nicht in Pips)
# ──────────────────────────────

TP_POINTS: float        = 36.0   # Take Profit Abstand (z.B. +36$ über Entry bei Long)
SL_BASE_POINTS: float   = 3.5    # initialer SL-Abstand (z.B. -3.5$ unter Entry bei Long)
STEP_DIST_POINTS: float = 5.0    # alle +5$ Gewinn → eine "Stufe"
STEP_SIZE_POINTS: float = 1.0    # pro Stufe rückt SL um 1$ Richtung Entry

# CFD-Größe pro Trade (je nach Risiko / Konto anpassen)
POSITION_SIZE: float = 0.5       # z.B. 0.5 €/Punkt – bitte anpassen


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
    Long-Variante der gestuften SL-Logik wie in deinem Pine-Skript:

    distFromEntry = current - entry
    steps = max(floor(distFromEntry / stepDist), 0)
    SL = entry - sl_base + steps * step_size
    SL nie über aktuellen Kurs ziehen.
    """
    dist_from_entry = current_price - entry_price
    steps = max(floor(dist_from_entry / step_dist), 0)
    sl = entry_price - sl_base + steps * step_size
    # Sicherheitsregel: SL nie über aktuellen Kurs
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
    Short-Variante der gestuften SL-Logik:

    distFromEntryShort = entry - current
    steps = max(floor(distFromEntryShort / stepDist), 0)
    SL = entry + sl_base - steps * step_size
    SL nie unter aktuellen Kurs ziehen.
    """
    dist_from_entry = entry_price - current_price
    steps = max(floor(dist_from_entry / step_dist), 0)
    sl = entry_price + sl_base - steps * step_size
    # Sicherheitsregel: SL nie unter aktuellen Kurs (bei Short)
    sl = max(sl, current_price)
    return sl


# ──────────────────────────────
# Haupteinstieg: wird vom Webhook-Service aufgerufen
# ──────────────────────────────

async def process_signal(tv_signal: TradingviewSignal) -> None:
    """
    TradingView-Webhook → Bot-Logik → Order bei IG.

    Aktuelle Strategie:
    - TradingView berechnet NUR die Entry-Bedingungen (Supertrend, EMA, ADX, Volumen, VWAP).
    - Wenn ein neues Signal kommt (long/short):
        * Wir eröffnen EINE Market-Position mit:
          - Take Profit = entry ± TP_POINTS
          - Stop Loss  = gestufter SL, initial noch in Stufe 0 (wie im Pine-Strategy-Skript)
    - Das spätere Nachziehen des SL in Stufen läuft über compute_step_sl_* und
      muss in einem separaten Update-Mechanismus eingebaut werden.
    """
    logger.info(f"[TRADE_ENGINE] processing signal: {tv_signal}")

    symbol = tv_signal.symbol           # z.B. "XAUUSD"
    side_raw = tv_signal.side.lower()   # "long" oder "short"
    entry_price = tv_signal.price       # Preis aus TradingView beim Signal

    # Sanity Check
    if side_raw not in ("long", "short"):
        logger.warning(f"[TRADE_ENGINE] Unknown side in signal: {side_raw}")
        return

    is_long = side_raw == "long"
    side = "buy" if is_long else "sell"     # für IG: BUY/SELL → wir mappen auf "buy"/"sell"

    # ──────────────────────────────
    # TP & initialer SL wie im Strategy-Skript
    # ──────────────────────────────
    if is_long:
        tp_price = entry_price + TP_POINTS
        # initial: current_price = entry_price → Stufe 0
        sl_price = compute_step_sl_long(entry_price, entry_price)
    else:
        tp_price = entry_price - TP_POINTS
        sl_price = compute_step_sl_short(entry_price, entry_price)

    logger.info(
        f"[TRADE_ENGINE] side={side_raw}, entry={entry_price}, "
        f"TP={tp_price}, initial SL={sl_price}, qty={POSITION_SIZE}"
    )

    # ──────────────────────────────
    # Order bei IG über broker_client
    # ──────────────────────────────
    try:
        order_response = await broker_client.place_market_order(
            symbol=symbol,
            side=side,                   # "buy" oder "sell"
            size=POSITION_SIZE,          # CFD-Größe
            order_type="market",
            take_profit=tp_price,        # geht als limitLevel an IG
            stop_loss=sl_price,          # geht als stopLevel an IG
        )
        logger.info(f"[TRADE_ENGINE] order response: {order_response}")
    except Exception as e:
        logger.exception(f"[TRADE_ENGINE] error while placing order: {e}")
