"""Source-grounded, versioned temporal memory. No automatic extraction from AI output."""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MemoryEvent(Base):
    __tablename__ = "memory_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class MemoryFact(Base):
    __tablename__ = "memory_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_event_id: Mapped[int] = mapped_column(
        ForeignKey("memory_events.id", ondelete="CASCADE"), nullable=False
    )
    subject_key: Mapped[str] = mapped_column(String(80), nullable=False)
    predicate: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    valid_from: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_memory_facts_user_slot_valid", "user_id", "subject_key", "predicate", "valid_to"),
        Index(
            "uq_memory_facts_active_slot", "user_id", "subject_key", "predicate", unique=True,
            postgresql_where=text("valid_to IS NULL"), sqlite_where=text("valid_to IS NULL")
        ),
    )
