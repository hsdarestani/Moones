from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    telegram_reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    audio_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    audio_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcript_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcription_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user = relationship("User", back_populates="messages")
