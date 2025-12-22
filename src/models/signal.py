# app/models/signal.py
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from db.database import Base  # dein declarative_base()
from models.tradingview_signal import TradingviewSignal


class Signal(Base):
    __tablename__ = "signals"

    id          = Column(Integer, primary_key=True, index=True)
    created_at  = Column(DateTime, server_default=func.now())
    symbol      = Column(String, index=True)
    side        = Column(String)
    timeframe   = Column(String)
    price       = Column(Float)
    status      = Column(String, default="new")
    raw_payload = Column(JSON)
    source      = Column(String, default="tradingview")
    # signal -> many trade groups
    trade_groups = relationship("TradeGroup", back_populates="signal")

    @classmethod
    def from_tradingview(cls, tv: TradingviewSignal) -> "Signal":
        return cls(
            symbol=tv.symbol,
            side=tv.side,
            timeframe=tv.timeframe,
            price=tv.price,
            status="new",
            source="tradingview",
            raw_payload=tv.model_dump(),
        )

