# src/services/trade_repo.py

from typing import Set, Optional, List
from sqlalchemy import func, exists

from db.database import SessionLocal
from models.bot_trade import BotTrade
from models.trade_group import TradeGroup


def create_trade_group(
    trade_group_id: str,
    symbol: str,
    side: str,
    timeframe: Optional[str] = None,
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
        total = db.query(func.count(BotTrade.id)).filter(BotTrade.trade_group_id == trade_group_id).scalar() or 0
        open_cnt = (
            db.query(func.count(BotTrade.id))
            .filter(BotTrade.trade_group_id == trade_group_id)
            .filter(BotTrade.status == "OPEN")
            .scalar()
            or 0
        )
        closed_cnt = total - open_cnt

        if total == 0:
            # sollte nicht passieren, aber sicherheitshalber
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


def has_active_trade_group(symbol: str) -> bool:
    """
    True, wenn für dieses Symbol/Epic mindestens eine TradeGroup OPEN oder PARTIAL ist.
    """
    db = SessionLocal()
    try:
        q = db.query(
            exists().where(
                (TradeGroup.symbol == symbol) &
                (TradeGroup.status.in_(("OPEN", "PARTIAL")))
            )
        )
        return bool(q.scalar())
    finally:
        db.close()