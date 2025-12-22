from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from db.database import Base  # ggf. anpassen


class BotTrade(Base):
    __tablename__ = "bot_trades"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(String, unique=True, index=True, nullable=False)

    # declared as FK for ORM
    trade_group_id = Column(String, ForeignKey("trade_groups.trade_group_id"), index=True, nullable=True)
    trade_group = relationship("TradeGroup", back_populates="bot_trades")

    tp_index = Column(Integer, nullable=True)

    symbol = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)

    entry_price = Column(Float, nullable=False)
    tp_price = Column(Float, nullable=False)
    initial_sl = Column(Float, nullable=False)

    status = Column(String, default="OPEN", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    closed_price = Column(Float, nullable=True)
    close_reason = Column(String, nullable=True)