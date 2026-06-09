from datetime import datetime
from enum import StrEnum
from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RelationshipStage(StrEnum):
    STRANGER = "STRANGER"
    FAMILIAR = "FAMILIAR"
    FRIEND = "FRIEND"
    ROMANTIC = "ROMANTIC"
    PARTNER = "PARTNER"


class Relationship(Base):
    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    intimacy: Mapped[float] = mapped_column(Float, default=0.05)
    attachment: Mapped[float] = mapped_column(Float, default=0.05)
    trust: Mapped[float] = mapped_column(Float, default=0.05)
    dependency: Mapped[float] = mapped_column(Float, default=0.0)
    attraction: Mapped[float] = mapped_column(Float, default=0.03)
    volatility: Mapped[float] = mapped_column(Float, default=0.2)
    stage: Mapped[str] = mapped_column(String(32), default=RelationshipStage.STRANGER.value)
    daily_streak: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="relationship_state")
