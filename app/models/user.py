from datetime import datetime
from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    onboarding_step: Mapped[str] = mapped_column(String(32), default="not_started")
    partner_gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(20), nullable=True)
    partner_age_range: Mapped[str | None] = mapped_column(String(16), nullable=True)
    partner_personality_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partner_interests: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="user", cascade="all, delete-orphan")
    relationship_state = relationship("Relationship", back_populates="user", uselist=False, cascade="all, delete-orphan")

    @property
    def onboarding_complete(self) -> bool:
        return self.onboarding_step == "complete"
