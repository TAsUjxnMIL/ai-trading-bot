from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime

from db.database import Base


class BotTrade(Base):
    __tablename__ = "bot_trades"

    id = Column(Integer, primary_key=True, index=True)

    deal_id = Column(String, unique=True, index=True, nullable=False)

    # NEW: group + tp_index
    trade_group_id = Column(String, index=True, nullable=True)  # später evtl. nullable=False
    tp_index = Column(Integer, nullable=True)  # 1 / 2 / 3

    symbol = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)  # "long" / "short"

    entry_price = Column(Float, nullable=False)
    tp_price = Column(Float, nullable=False)
    initial_sl = Column(Float, nullable=False)

    status = Column(String, default="OPEN", index=True)  # OPEN / CLOSED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Optional, aber praktisch
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    closed_price = Column(Float, nullable=True)