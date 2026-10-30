"""Opt-in semantic index: vectors belong to original, user-owned message rows.

No duplicate archive text is stored. Deleting a message cascades to its vector.
SQLite/vanilla PostgreSQL compatible; vector JSON is intentional for small pilots.
"""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class MemoryEmbedding(Base):
    __tablename__ = "memory_embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    vector: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "source_message_id", "model_id", name="uq_memory_embeddings_source_model"),
        Index("ix_memory_embeddings_user_model", "user_id", "model_id"),
    )
