from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

CAMPAIGN_STATUSES = {"draft", "previewed", "running", "completed", "partially_failed", "cancelled"}
RECIPIENT_STATUSES = {"pending", "credited", "already_credited", "failed", "excluded"}

class AdminCoinCampaign(Base):
    __tablename__ = "admin_coin_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    campaign_key: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    admin_note: Mapped[str] = mapped_column(Text, nullable=False)
    amount_coins: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audience_type: Mapped[str] = mapped_column(String(64), default="all_users", nullable=False)
    audience_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    created_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True, index=True)
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    target_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credited_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_credited_coins: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    recipients = relationship("AdminCoinCampaignRecipient", back_populates="campaign", cascade="all, delete-orphan")

class AdminCoinCampaignRecipient(Base):
    __tablename__ = "admin_coin_campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id", name="uq_admin_coin_campaign_recipient"),)
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("admin_coin_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    wallet_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("wallet_transactions.id"), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    campaign = relationship("AdminCoinCampaign", back_populates="recipients")
