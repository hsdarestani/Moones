from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.addon import UserAddon
from app.models.billing import LegacySubscriptionPreservation, UsageCharge
from app.models.payment import PaymentReceipt
from app.models.wallet import Wallet, WalletTransaction


@dataclass(frozen=True)
class LedgerRow:
    time: datetime | None
    category: str
    reason: str
    credit: int
    debit: int
    balance_after: int | None
    linked_object: str
    idempotency_key_summary: str
    source: str | None = None


def _idem(value: str | None) -> str:
    if not value:
        return "—"
    return value if len(value) <= 24 else f"{value[:12]}…{value[-8:]}"


def categorize_wallet_transaction(tx: WalletTransaction) -> str:
    reason = (tx.reason or "").lower()
    if "welcome" in reason or "signup" in reason:
        return "welcome_credit"
    if "bulk_gift" in reason or "gift" in reason or "promo" in reason:
        return "administrative_gift"
    if "refund" in reason or tx.type == "refund":
        return "refund"
    if "addon" in reason:
        return "addon_purchase"
    if "usage" in reason or tx.usage_charge_id:
        return "usage_charge"
    if "admin" in reason or tx.type == "adjustment":
        return "wallet_adjustment"
    return "wallet_transaction"


class AdminFinancialLedgerService:
    """Read-only projection over authoritative financial tables."""

    def rows(self, db: Session, user_id: int, *, limit: int = 50, offset: int = 0) -> list[LedgerRow]:
        rows: list[LedgerRow] = []
        txs = db.scalars(select(WalletTransaction).where(WalletTransaction.user_id == user_id)).all()
        for tx in txs:
            is_credit = tx.type in {"credit", "refund"} or (tx.type == "adjustment" and tx.amount_coins >= 0)
            rows.append(LedgerRow(tx.created_at, categorize_wallet_transaction(tx), tx.reason, tx.amount_coins if is_credit else 0, 0 if is_credit else tx.amount_coins, tx.balance_after, f"wallet_transaction:{tx.id}", _idem(tx.idempotency_key), (tx.metadata_json or {}).get("source") or (tx.metadata_json or {}).get("campaign_key")))
        charges = db.scalars(select(UsageCharge).where(UsageCharge.user_id == user_id)).all()
        seen_charge_ids = {tx.usage_charge_id for tx in txs if tx.usage_charge_id}
        for charge in charges:
            if charge.id not in seen_charge_ids:
                rows.append(LedgerRow(charge.settled_at or charge.created_at, "usage_charge", charge.feature, 0, int(charge.charged_coins or charge.reserved_coins or 0), None, f"usage_charge:{charge.id}", _idem(charge.idempotency_key), f"{charge.provider}/{charge.model}"))
            if charge.refunded_coins:
                rows.append(LedgerRow(charge.refunded_at or charge.settled_at or charge.created_at, "refund", f"refund:{charge.feature}", int(charge.refunded_coins or 0), 0, None, f"usage_charge:{charge.id}", _idem(charge.idempotency_key), charge.error[:80] if charge.error else None))
        for receipt in db.scalars(select(PaymentReceipt).where(PaymentReceipt.user_id == user_id)).all():
            if receipt.status == "approved":
                category = "addon_purchase" if receipt.purpose == "addon" or receipt.addon_key else "payment_receipt_approval"
                rows.append(LedgerRow(receipt.reviewed_at or receipt.created_at, category, receipt.purpose or "wallet_topup", int(receipt.approved_coins or receipt.requested_coins or 0), 0, None, f"payment_receipt:{receipt.id}", "—", f"admin:{receipt.admin_id}" if receipt.admin_id else None))
        for addon in db.scalars(select(UserAddon).where(UserAddon.user_id == user_id)).all():
            if addon.source != "manual_payment" or addon.payment_receipt_id is None:
                rows.append(LedgerRow(addon.activated_at or addon.created_at, "addon_purchase", addon.addon_key, 0, int(addon.price_paid_coins or 0), None, f"user_addon:{addon.id}", "—", addon.source))
        for legacy in db.scalars(select(LegacySubscriptionPreservation).where(LegacySubscriptionPreservation.user_id == user_id)).all():
            rows.append(LedgerRow(legacy.created_at, "legacy_subscription_exemption", legacy.preservation_policy, int(legacy.converted_subscription_value or 0), 0, None, f"legacy_subscription_preservation:{legacy.id}", "—", legacy.plan))
        rows.sort(key=lambda r: (r.time or datetime.min, r.linked_object), reverse=True)
        return rows[offset: offset + limit]

    def reconciliation(self, db: Session, user_id: int) -> dict[str, Any]:
        wallet = db.scalar(select(Wallet).where(Wallet.user_id == user_id))
        latest_tx = db.scalar(select(WalletTransaction).where(WalletTransaction.user_id == user_id).order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc()).limit(1))
        total_credits = db.scalar(select(func.coalesce(func.sum(WalletTransaction.amount_coins), 0)).where(WalletTransaction.user_id == user_id, WalletTransaction.type.in_(["credit", "refund"]))) or 0
        total_debits = db.scalar(select(func.coalesce(func.sum(WalletTransaction.amount_coins), 0)).where(WalletTransaction.user_id == user_id, WalletTransaction.type == "debit")) or 0
        unsettled = db.scalar(select(func.coalesce(func.sum(UsageCharge.reserved_coins), 0)).where(UsageCharge.user_id == user_id, UsageCharge.status.in_(["reserved", "pending"]))) or 0
        current = wallet.balance_coins if wallet else 0
        latest_balance = latest_tx.balance_after if latest_tx else current
        return {"wallet_balance": current, "latest_balance_after": latest_balance, "total_credits": int(total_credits), "total_debits": int(total_debits), "unsettled_reservations": int(unsettled), "mismatch": latest_balance != current}
