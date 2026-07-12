from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.billing import UsageCharge
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.services.coin_pricing_service import CoinPricingService, CoinQuote
from app.services.wallet_service import WalletService

class InsufficientCoins(Exception):
    def __init__(self, required: int, balance: int):
        self.required = required; self.balance = balance
        super().__init__(f"insufficient coins: required={required} balance={balance}")

def new_correlation_id(prefix="usage") -> str: return f"{prefix}:{uuid4().hex}"

class UsageBillingService:
    def __init__(self, pricing: CoinPricingService | None = None): self.pricing = pricing or CoinPricingService(); self.wallets = WalletService()
    def reserve(self, db: Session, *, user: User, idempotency_key: str, feature: str, provider: str, model: str, quote: CoinQuote, correlation_id: str | None=None, metadata: dict | None=None) -> UsageCharge:
        existing = db.scalar(select(UsageCharge).where(UsageCharge.idempotency_key == idempotency_key))
        if existing: return existing
        wallet = self.wallets.get_or_create_wallet(db, user)
        if quote.charged_coins > wallet.balance_coins: raise InsufficientCoins(quote.charged_coins, wallet.balance_coins)
        wallet.balance_coins -= quote.charged_coins; wallet.total_spent_coins += quote.charged_coins
        charge = UsageCharge(idempotency_key=idempotency_key, correlation_id=correlation_id, user_id=user.id, wallet_id=wallet.id, feature=feature, provider=provider, model=model, status="reserved", reserved_coins=quote.charged_coins, charged_coins=0, estimated_cost_usd=quote.provider_cost_usd, exchange_rate_toman=quote.exchange_rate_toman, profit_margin_percent=quote.profit_margin_percent, toman_per_coin=quote.toman_per_coin, pricing_snapshot_json=quote.pricing_snapshot, request_metadata_json=metadata)
        db.add(charge); db.flush()
        db.add(WalletTransaction(user_id=user.id, wallet_id=wallet.id, type="debit", amount_coins=quote.charged_coins, balance_after=wallet.balance_coins, reason="usage_reservation", metadata_json={"feature":feature}, unit="coin", idempotency_key=f"reserve:{idempotency_key}", usage_charge_id=charge.id, correlation_id=correlation_id))
        db.flush(); return charge
    def settle(self, db: Session, *, charge: UsageCharge, actual_quote: CoinQuote, usage_event=None) -> UsageCharge:
        charge = db.get(UsageCharge, charge.id) or charge
        if charge.status == "settled": return charge
        wallet = self.wallets.get_or_create_wallet(db, charge.user)
        actual = actual_quote.charged_coins
        if actual < charge.reserved_coins:
            refund = charge.reserved_coins - actual; wallet.balance_coins += refund; wallet.total_spent_coins -= refund; charge.refunded_coins += refund
            db.add(WalletTransaction(user_id=charge.user_id, wallet_id=wallet.id, type="refund", amount_coins=refund, balance_after=wallet.balance_coins, reason="usage_reservation_refund", metadata_json={"feature":charge.feature}, unit="coin", idempotency_key=f"settle-refund:{charge.id}", usage_charge_id=charge.id, correlation_id=charge.correlation_id))
        elif actual > charge.reserved_coins:
            extra = actual - charge.reserved_coins
            if wallet.balance_coins < extra: actual = charge.reserved_coins
            else:
                wallet.balance_coins -= extra; wallet.total_spent_coins += extra
                db.add(WalletTransaction(user_id=charge.user_id, wallet_id=wallet.id, type="debit", amount_coins=extra, balance_after=wallet.balance_coins, reason="usage_settlement_extra", metadata_json={"feature":charge.feature}, unit="coin", idempotency_key=f"settle-extra:{charge.id}", usage_charge_id=charge.id, correlation_id=charge.correlation_id))
        charge.status="settled"; charge.charged_coins=actual; charge.actual_cost_usd=actual_quote.provider_cost_usd; charge.exchange_rate_toman=actual_quote.exchange_rate_toman; charge.profit_margin_percent=actual_quote.profit_margin_percent; charge.pricing_snapshot_json=actual_quote.pricing_snapshot; charge.settled_at=datetime.utcnow()
        if usage_event is not None:
            usage_event.usage_charge_id=charge.id; usage_event.charged_coins=actual; usage_event.exchange_rate_toman=actual_quote.exchange_rate_toman; usage_event.profit_margin_percent=actual_quote.profit_margin_percent; usage_event.pricing_registry_version=actual_quote.pricing_snapshot.get("registry_version"); usage_event.correlation_id=charge.correlation_id; charge.usage_event_id=usage_event.id
        db.flush(); return charge
    def refund(self, db: Session, *, charge: UsageCharge, error: str | None=None) -> UsageCharge:
        charge = db.get(UsageCharge, charge.id) or charge
        if charge.status == "refunded": return charge
        wallet = self.wallets.get_or_create_wallet(db, charge.user); amount = max(0, charge.reserved_coins - charge.refunded_coins)
        if amount:
            wallet.balance_coins += amount; wallet.total_spent_coins -= amount; charge.refunded_coins += amount
            db.add(WalletTransaction(user_id=charge.user_id, wallet_id=wallet.id, type="refund", amount_coins=amount, balance_after=wallet.balance_coins, reason="usage_provider_failure", metadata_json={"error":(error or "")[:200]}, unit="coin", idempotency_key=f"refund:{charge.id}", usage_charge_id=charge.id, correlation_id=charge.correlation_id))
        charge.status="refunded"; charge.error=(error or "")[:500]; charge.refunded_at=datetime.utcnow(); db.flush(); return charge
