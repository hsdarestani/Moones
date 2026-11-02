"""User-level semantic consent and provider usage without logging private text."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class MemoryPrivacyChoice(Base):
    __tablename__ = "memory_privacy_choices"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    semantic_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class MemoryEmbeddingUsage(Base):
    __tablename__ = "memory_embedding_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    request_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    price_usd_per_million: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    pricing_basis: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("ix_memory_embedding_usage_user_created", "user_id", "created_at"),)
