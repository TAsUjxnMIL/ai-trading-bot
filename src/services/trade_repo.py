from db.database import SessionLocal
from models.bot_trade import BotTrade
from typing import Set


def add_open_trade(
    deal_id: str,
    symbol: str,
    side: str,
    entry_price: float,
    tp_price: float,
    initial_sl: float,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            BotTrade(
                deal_id=deal_id,
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