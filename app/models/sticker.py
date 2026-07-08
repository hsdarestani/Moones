from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


class StickerPack(Base):
    __tablename__ = "sticker_packs"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_set_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    items = relationship("StickerItem", back_populates="pack", cascade="all, delete-orphan")


class StickerItem(Base):
    __tablename__ = "sticker_items"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    pack_id: Mapped[int | None] = mapped_column(ForeignKey("sticker_packs.id", ondelete="SET NULL"), nullable=True, index=True)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    emoji: Mapped[str | None] = mapped_column(String(32), nullable=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    usage_context: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relationship_stage_min: Mapped[str | None] = mapped_column(String(32), nullable=True)
    personality_match: Mapped[str | None] = mapped_column(String(64), nullable=True)
    persona_gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    persona_style: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Metadata-driven sticker catalog fields. Existing legacy fields above remain
    # for compatibility with Telegram capture and old sending flows.
    key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), default="normal", nullable=False, index=True)
    meaning: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_emojis: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mood: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    gender_target: Mapped[str] = mapped_column(String(16), default="neutral", nullable=False, index=True)
    relationship_stages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    probability: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    pack = relationship("StickerPack", back_populates="items")

    @property
    def file_id(self) -> str:
        return self.telegram_file_id

    @file_id.setter
    def file_id(self, value: str) -> None:
        self.telegram_file_id = value
