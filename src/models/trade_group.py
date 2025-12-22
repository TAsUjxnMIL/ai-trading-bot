from datetime import datetime
import uuid

from sqlalchemy import Column, String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import relationship
from db.database import Base


class TradeGroup(Base):
    __tablename__ = "trade_groups"

    # UUID als String (SQLite-friendly)
    trade_group_id = Column(
        String,
        primary_key=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    # Link to Signal that produced this group
    signal_id = Column(Integer, ForeignKey("signals.id"), index=True, nullable=True)
    
    symbol = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)        # "long" / "short"
    timeframe = Column(String, nullable=True)    # z.B. "5m" / "15m" (falls du es hast)
    status = Column(String, default="OPEN", index=True)  # OPEN / PARTIAL / CLOSED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ORM relations (no DB columns)
    signal = relationship("Signal", back_populates="trade_groups")
    bot_trades = relationship("BotTrade", back_populates="trade_group")