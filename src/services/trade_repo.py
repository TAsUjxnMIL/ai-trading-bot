# src/services/trade_repo.py

from typing import Set, Optional
from datetime import datetime, timedelta

from sqlalchemy import func, exists

from db.database import SessionLocal
from models.bot_trade import BotTrade
from models.trade_group import TradeGroup


def create_trade_group(
    trade_group_id: str,
    symbol: str,
    side: str,
    timeframe: Optional[str] = None,
    signal_id: Optional[int] = None,
) -> None:
    """
    Erstellt einen TradeGroup-Eintrag (einmal pro TradingView Signal / Trade-Idee).
    """
    db = SessionLocal()
    try:
        db.add(
            TradeGroup(
                trade_group_id=trade_group_id,
                symbol=symbol,
                side=side,
                timeframe=timeframe,
                status="OPEN",
                signal_id=signal_id,
            )
        )
        db.commit()
    finally:
        db.close()


def add_open_trade(
    deal_id: str,
    trade_group_id: str,
    tp_index: int,
    symbol: str,
    side: str,
    entry_price: float,
    tp_price: float,
    initial_sl: float,
) -> None:
    """
    Speichert eine geöffnete Bot-Position (eine IG-Order / Deal).
    """
    db = SessionLocal()
    try:
        db.add(
            BotTrade(
                deal_id=deal_id,
                trade_group_id=trade_group_id,
                tp_index=tp_index,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                tp_price=tp_price,
                initial_sl=initial_sl,
                status="OPEN",
            )
        )
        db.commit()
    finally:
        db.close()


def get_open_bot_deal_ids() -> Set[str]:
    db = SessionLocal()
    try:
        rows = db.query(BotTrade.deal_id).filter(BotTrade.status == "OPEN").all()
        return {r[0] for r in rows}
    finally:
        db.close()


def mark_trades_closed(deal_ids: Set[str]) -> None:
    if not deal_ids:
        return

    db = SessionLocal()
    try:
        db.query(BotTrade).filter(BotTrade.deal_id.in_(list(deal_ids))).update(
            {"status": "CLOSED"},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


def get_trade_group_ids_for_deals(deal_ids: Set[str]) -> Set[str]:
    if not deal_ids:
        return set()

    db = SessionLocal()
    try:
        rows = (
            db.query(BotTrade.trade_group_id)
            .filter(BotTrade.deal_id.in_(list(deal_ids)))
            .filter(BotTrade.trade_group_id.isnot(None))
            .distinct()
            .all()
        )
        return {r[0] for r in rows if r and r[0]}
    finally:
        db.close()


def recompute_trade_group_status(trade_group_id: str) -> None:
    db = SessionLocal()
    try:
        total = (
            db.query(func.count(BotTrade.id))
            .filter(BotTrade.trade_group_id == trade_group_id)
            .scalar()
            or 0
        )
        open_cnt = (
            db.query(func.count(BotTrade.id))
            .filter(BotTrade.trade_group_id == trade_group_id)
            .filter(BotTrade.status == "OPEN")
            .scalar()
            or 0
        )
        closed_cnt = total - open_cnt

        if total == 0:
            # Gruppe existiert, aber keine Trades dazu -> lieber CLOSED (oder ERROR)
            new_status = "CLOSED"
        elif open_cnt == 0:
            new_status = "CLOSED"
        elif closed_cnt > 0:
            new_status = "PARTIAL"
        else:
            new_status = "OPEN"

        db.query(TradeGroup).filter(TradeGroup.trade_group_id == trade_group_id).update(
            {"status": new_status},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


def set_trade_group_status(trade_group_id: str, status: str) -> None:
    db = SessionLocal()
    try:
        db.query(TradeGroup).filter(TradeGroup.trade_group_id == trade_group_id).update(
            {"status": status},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


def has_active_trade_group(symbol: str) -> bool:
    """
    True, wenn für dieses Symbol/Epic mindestens eine TradeGroup OPEN oder PARTIAL ist.
    """
    db = SessionLocal()
    try:
        q = db.query(
            exists().where(
                (TradeGroup.symbol == symbol)
                & (TradeGroup.status.in_(("OPEN", "PARTIAL")))
            )
        )
        return bool(q.scalar())
    finally:
        db.close()


def get_active_trade_group_ids() -> Set[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(TradeGroup.trade_group_id)
            .filter(TradeGroup.status.in_(("OPEN", "PARTIAL")))
            .all()
        )
        return {r[0] for r in rows if r and r[0]}
    finally:
        db.close()


def count_open_trades_in_group(trade_group_id: str) -> int:
    db = SessionLocal()
    try:
        cnt = (
            db.query(func.count(BotTrade.id))
            .filter(BotTrade.trade_group_id == trade_group_id)
            .filter(BotTrade.status == "OPEN")
            .scalar()
        )
        return int(cnt or 0)
    finally:
        db.close()


def count_total_trades_in_group(trade_group_id: str) -> int:
    db = SessionLocal()
    try:
        cnt = (
            db.query(func.count(BotTrade.id))
            .filter(BotTrade.trade_group_id == trade_group_id)
            .scalar()
        )
        return int(cnt or 0)
    finally:
        db.close()


def get_stale_empty_trade_groups(grace_seconds: int = 60) -> Set[str]:
    """
    Garbage Collector Helper:
    Liefert TradeGroup IDs, die:
      - status OPEN oder PARTIAL sind
      - älter als grace_seconds
      - und NOCH NIE BotTrades bekommen haben (total=0)

    Wichtig: Das verhindert Race Conditions zwischen create_trade_group() und add_open_trade().
    """
    cutoff = datetime.utcnow() - timedelta(seconds=grace_seconds)

    db = SessionLocal()
    try:
        # NOT EXISTS BotTrade rows for this group
        no_trades_subq = ~exists().where(BotTrade.trade_group_id == TradeGroup.trade_group_id)

        rows = (
            db.query(TradeGroup.trade_group_id)
            .filter(TradeGroup.status.in_(("OPEN", "PARTIAL")))
            .filter(TradeGroup.created_at <= cutoff)  # TradeGroup muss created_at haben
            .filter(no_trades_subq)
            .all()
        )
        return {r[0] for r in rows if r and r[0]}
    finally:
        db.close()


def get_trade_by_deal_id(deal_id: str) -> Optional[BotTrade]:
    """
    Returns the BotTrade row for a given IG position deal_id (or None if not found).
    """
    db = SessionLocal()
    try:
        return (
            db.query(BotTrade)
            .filter(BotTrade.deal_id == deal_id)
            .one_or_none()
        )
    finally:
        db.close()


def get_open_trades_in_group(
    trade_group_id: str,
    exclude_deal_id: Optional[str] = None,
) -> list[BotTrade]:
    """
    Returns all OPEN BotTrades in a trade group. Optionally exclude one deal_id
    (useful when TP1 trade just closed and you want "remaining" trades).
    """
    db = SessionLocal()
    try:
        q = (
            db.query(BotTrade)
            .filter(BotTrade.trade_group_id == trade_group_id)
            .filter(BotTrade.status == "OPEN")
        )
        if exclude_deal_id:
            q = q.filter(BotTrade.deal_id != exclude_deal_id)

        return q.order_by(BotTrade.tp_index.asc()).all()
    finally:
        db.close()


def set_trade_close_meta(
    deal_id: str,
    closed_price: Optional[float],
    close_reason: Optional[str],
    closed_at: Optional[datetime] = None,  # optional, kannst du None lassen
) -> None:
    db = SessionLocal()
    try:
        update_data = {}
        if closed_price is not None:
            update_data["closed_price"] = float(closed_price)
        if close_reason is not None:
            update_data["close_reason"] = str(close_reason)
        if closed_at is not None:
            update_data["closed_at"] = closed_at

        if not update_data:
            return

        db.query(BotTrade).filter(BotTrade.deal_id == deal_id).update(
            update_data,
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()


def get_trade_in_group_by_tp_index(trade_group_id: str, tp_index: int) -> Optional[BotTrade]:
    """
    Returns the BotTrade in the given group with the given tp_index (e.g. TP1 trade),
    regardless of OPEN/CLOSED status (because TP1 is already closed when TP2 hits).
    """
    db = SessionLocal()
    try:
        return (
            db.query(BotTrade)
            .filter(BotTrade.trade_group_id == trade_group_id)
            .filter(BotTrade.tp_index == tp_index)
            .one_or_none()
        )
    finally:
        db.close()
