from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from app.models.billing import LegacySubscriptionPreservation, UsageCharge
from app.models.subscription import Subscription
from app.models.user import User
from app.models.wallet import WalletTransaction
from app.services.coin_pricing_service import CoinPricingService, CoinQuote
from app.services.wallet_service import WalletService

LEGACY_SUBSCRIPTION_EXEMPT_FEATURES = frozenset({"chat", "stt", "vision", "tts"})

class InsufficientCoins(Exception):
    def __init__(self, required: int, balance: int):
        self.required = required; self.balance = balance
        super().__init__(f"insufficient coins: required={required} balance={balance}")

def new_correlation_id(prefix="usage") -> str: return f"{prefix}:{uuid4().hex}"

def _utcnow() -> datetime:
    return datetime.utcnow()

def _is_exempt_metadata(metadata: dict | None) -> bool:
    return bool(metadata and metadata.get("billing_exempt") is True and metadata.get("billing_exempt_reason") == "legacy_subscription_preserved")

class LegacySubscriptionBillingExemptionPolicy:
    """Central policy for migration-0030 preserved legacy subscription exemptions."""

    covered_features = LEGACY_SUBSCRIPTION_EXEMPT_FEATURES

    def exemption_metadata(self, db: Session, *, user: User, feature: str, now: datetime | None = None) -> dict | None:
        if feature not in self.covered_features:
            return None
        now = now or _utcnow()
        table_names = set(inspect(db.connection()).get_table_names())
        if "legacy_subscription_preservations" not in table_names or "subscriptions" not in table_names:
            return None
        row = db.execute(
            select(LegacySubscriptionPreservation, Subscription)
            .join(Subscription, Subscription.id == LegacySubscriptionPreservation.subscription_id)
            .where(
                LegacySubscriptionPreservation.user_id == user.id,
                LegacySubscriptionPreservation.preservation_policy == "preserve_until_expiry",
                Subscription.user_id == user.id,
                Subscription.status.in_(("active", "trialing")),
                Subscription.plan != "free",
            )
        ).first()
        if row is None:
            return None
        preservation, subscription = row
        expires_at = subscription.expires_at
        if expires_at is not None and expires_at <= now:
            return None
        return {
            "billing_exempt": True,
            "billing_exempt_reason": "legacy_subscription_preserved",
            "legacy_subscription_id": subscription.id,
            "legacy_plan": subscription.plan,
            "legacy_subscription_expires_at": expires_at.isoformat() if expires_at else None,
            "legacy_preservation_id": preservation.id,
        }

class UsageBillingService:
    def __init__(self, pricing: CoinPricingService | None = None, exemption_policy: LegacySubscriptionBillingExemptionPolicy | None = None):
        self.pricing = pricing or CoinPricingService(); self.wallets = WalletService(); self.exemption_policy = exemption_policy or LegacySubscriptionBillingExemptionPolicy()

    def reserve(self, db: Session, *, user: User, idempotency_key: str, feature: str, provider: str, model: str, quote: CoinQuote, correlation_id: str | None=None, metadata: dict | None=None) -> UsageCharge:
        existing = db.scalar(select(UsageCharge).where(UsageCharge.idempotency_key == idempotency_key))
        if existing: return existing
        wallet = self.wallets.get_or_create_wallet(db, user)
        exemption = self.exemption_policy.exemption_metadata(db, user=user, feature=feature)
        request_metadata = dict(metadata or {})
        if exemption:
            request_metadata.update(exemption)
            charge = UsageCharge(idempotency_key=idempotency_key, correlation_id=correlation_id, user_id=user.id, wallet_id=wallet.id, feature=feature, provider=provider, model=model, status="reserved", reserved_coins=0, charged_coins=0, estimated_cost_usd=quote.provider_cost_usd, exchange_rate_toman=quote.exchange_rate_toman, profit_margin_percent=quote.profit_margin_percent, toman_per_coin=quote.toman_per_coin, pricing_snapshot_json=quote.pricing_snapshot, request_metadata_json=request_metadata)
            db.add(charge); db.flush(); return charge
        if quote.charged_coins > wallet.balance_coins: raise InsufficientCoins(quote.charged_coins, wallet.balance_coins)
        wallet.balance_coins -= quote.charged_coins; wallet.total_spent_coins += quote.charged_coins
        charge = UsageCharge(idempotency_key=idempotency_key, correlation_id=correlation_id, user_id=user.id, wallet_id=wallet.id, feature=feature, provider=provider, model=model, status="reserved", reserved_coins=quote.charged_coins, charged_coins=0, estimated_cost_usd=quote.provider_cost_usd, exchange_rate_toman=quote.exchange_rate_toman, profit_margin_percent=quote.profit_margin_percent, toman_per_coin=quote.toman_per_coin, pricing_snapshot_json=quote.pricing_snapshot, request_metadata_json=request_metadata or None)
        db.add(charge); db.flush()
        db.add(WalletTransaction(user_id=user.id, wallet_id=wallet.id, type="debit", amount_coins=quote.charged_coins, balance_after=wallet.balance_coins, reason="usage_reservation", metadata_json={"feature":feature}, unit="coin", idempotency_key=f"reserve:{idempotency_key}", usage_charge_id=charge.id, correlation_id=correlation_id))
        db.flush(); return charge

    def settle(self, db: Session, *, charge: UsageCharge, actual_quote: CoinQuote, usage_event=None) -> UsageCharge:
        charge = db.get(UsageCharge, charge.id) or charge
        if charge.status == "settled": return charge
        exempt = _is_exempt_metadata(charge.request_metadata_json)
        actual = 0 if exempt else actual_quote.charged_coins
        if not exempt:
            wallet = self.wallets.get_or_create_wallet(db, charge.user)
            if actual < charge.reserved_coins:
                refund = charge.reserved_coins - actual; wallet.balance_coins += refund; wallet.total_spent_coins -= refund; charge.refunded_coins += refund
                db.add(WalletTransaction(user_id=charge.user_id, wallet_id=wallet.id, type="refund", amount_coins=refund, balance_after=wallet.balance_coins, reason="usage_reservation_refund", metadata_json={"feature":charge.feature}, unit="coin", idempotency_key=f"settle-refund:{charge.id}", usage_charge_id=charge.id, correlation_id=charge.correlation_id))
            elif actual > charge.reserved_coins:
                extra = actual - charge.reserved_coins
                if wallet.balance_coins < extra: actual = charge.reserved_coins
                else:
                    wallet.balance_coins -= extra; wallet.total_spent_coins += extra
                    db.add(WalletTransaction(user_id=charge.user_id, wallet_id=wallet.id, type="debit", amount_coins=extra, balance_after=wallet.balance_coins, reason="usage_settlement_extra", metadata_json={"feature":charge.feature}, unit="coin", idempotency_key=f"settle-extra:{charge.id}", usage_charge_id=charge.id, correlation_id=charge.correlation_id))
        charge.status="settled"; charge.charged_coins=actual; charge.actual_cost_usd=actual_quote.provider_cost_usd; charge.exchange_rate_toman=actual_quote.exchange_rate_toman; charge.profit_margin_percent=actual_quote.profit_margin_percent; charge.pricing_snapshot_json=actual_quote.pricing_snapshot; charge.settled_at=_utcnow()
        if usage_event is not None:
            usage_event.usage_charge_id=charge.id; usage_event.charged_coins=actual; usage_event.exchange_rate_toman=actual_quote.exchange_rate_toman; usage_event.profit_margin_percent=actual_quote.profit_margin_percent; usage_event.pricing_registry_version=actual_quote.pricing_snapshot.get("registry_version"); usage_event.correlation_id=charge.correlation_id; charge.usage_event_id=usage_event.id
        db.flush(); return charge

    def refund(self, db: Session, *, charge: UsageCharge, error: str | None=None) -> UsageCharge:
        charge = db.get(UsageCharge, charge.id) or charge
        if charge.status == "refunded": return charge
        if not _is_exempt_metadata(charge.request_metadata_json):
            wallet = self.wallets.get_or_create_wallet(db, charge.user); amount = max(0, charge.reserved_coins - charge.refunded_coins)
            if amount:
                wallet.balance_coins += amount; wallet.total_spent_coins -= amount; charge.refunded_coins += amount
                db.add(WalletTransaction(user_id=charge.user_id, wallet_id=wallet.id, type="refund", amount_coins=amount, balance_after=wallet.balance_coins, reason="usage_provider_failure", metadata_json={"error":(error or "")[:200]}, unit="coin", idempotency_key=f"refund:{charge.id}", usage_charge_id=charge.id, correlation_id=charge.correlation_id))
        charge.status="refunded"; charge.error=(error or "")[:500]; charge.refunded_at=_utcnow(); db.flush(); return charge
