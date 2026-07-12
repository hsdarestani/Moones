from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AiUsageEvent(Base):
    __tablename__ = "ai_usage_events"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True, index=True)
    media_message_id: Mapped[int | None] = mapped_column(ForeignKey("media_messages.id"), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(Text, default="venice", nullable=False)
    feature: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    model: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_seconds: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unit_input_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    unit_output_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    unit_audio_second_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    unit_image_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    unit_character_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    cost_toman: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="success", nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usage_charge_id: Mapped[int | None] = mapped_column(ForeignKey("usage_charges.id"), nullable=True, index=True)
    charged_coins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exchange_rate_toman: Mapped[object | None] = mapped_column(Numeric, nullable=True)
    profit_margin_percent: Mapped[object | None] = mapped_column(Numeric, nullable=True)
    pricing_registry_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User")
    message = relationship("Message")
    media_message = relationship("MediaMessage")


class AiUsageDailyRollup(Base):
    __tablename__ = "ai_usage_daily_rollups"
    __table_args__ = (UniqueConstraint("date", "user_id", "plan", "feature", "model", name="uq_ai_usage_rollup_dim"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_seconds: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    cost_toman: Mapped[float] = mapped_column(Numeric, default=0, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
