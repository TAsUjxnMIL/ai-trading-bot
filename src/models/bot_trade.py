from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime

from db.database import Base


class BotTrade(Base):
    __tablename__ = "bot_trades"

    id = Column(Integer, primary_key=True, index=True)

    deal_id = Column(String, unique=True, index=True, nullable=False)
    symbol = Column(String, index=True, nullable=False)
    side = Column(String, nullable=False)  # "long" / "short"

    entry_price = Column(Float, nullable=False)
    tp_price = Column(Float, nullable=False)
    initial_sl = Column(Float, nullable=False)

    status = Column(String, default="OPEN", index=True)  # OPEN / CLOSED
    created_at = Column(DateTime, default=datetime.utcnow)