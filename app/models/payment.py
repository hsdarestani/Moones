from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class PaymentReceipt(Base):
    __tablename__ = "payment_receipts"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_toman: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paid_toman: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_coins: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_coins: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), default="wallet_topup", nullable=False)
    addon_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user = relationship("User", back_populates="payment_receipts")
