from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base

class UsageCharge(Base):
    __tablename__ = "usage_charges"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), index=True)
    usage_event_id: Mapped[int | None] = mapped_column(ForeignKey("ai_usage_events.id"), nullable=True, index=True)
    feature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="reserved", nullable=False, index=True)
    reserved_coins: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    charged_coins: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    refunded_coins: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    estimated_cost_usd: Mapped[object] = mapped_column(Numeric(18, 8), default=0, nullable=False)
    actual_cost_usd: Mapped[object] = mapped_column(Numeric(18, 8), default=0, nullable=False)
    exchange_rate_toman: Mapped[object] = mapped_column(Numeric(18, 4), default=60000, nullable=False)
    profit_margin_percent: Mapped[object] = mapped_column(Numeric(8, 2), default=100, nullable=False)
    toman_per_coin: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    pricing_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user = relationship("User")
    wallet = relationship("Wallet")
    usage_event = relationship("AiUsageEvent", foreign_keys=[usage_event_id])

class WalletCurrencyMigration(Base):
    __tablename__ = "wallet_currency_migrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"), unique=True, nullable=False, index=True)
    previous_balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    previous_total_added: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    previous_total_spent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    converted_balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    converted_total_added: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    converted_total_spent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    conversion_denominator: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    converted_subscription_value: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    migration_version: Mapped[str] = mapped_column(String(64), default="0030_coin_usage_billing", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
