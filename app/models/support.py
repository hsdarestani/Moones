from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    admin_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    admin_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_message: Mapped[str] = mapped_column(Text)
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="support_messages")
