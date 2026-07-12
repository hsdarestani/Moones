from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import User, Wallet, WalletTransaction, UsageCharge, PaymentReceipt, UserAddon
from app.models.billing import LegacySubscriptionPreservation
from app.models.subscription import Subscription
from app.services.admin_user_360_service import AdminFinancialLedgerService


def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, UsageCharge.__table__, PaymentReceipt.__table__, UserAddon.__table__, Subscription.__table__, LegacySubscriptionPreservation.__table__])
    return sessionmaker(bind=e)()


def test_ledger_ordering_gifts_topups_refunds_and_legacy_are_projected_read_only():
    s = db(); now = datetime.utcnow(); u = User(telegram_id=1); s.add(u); s.flush(); w = Wallet(user_id=u.id, balance_coins=80); s.add(w); s.flush()
    s.add(WalletTransaction(user_id=u.id, wallet_id=w.id, type="credit", amount_coins=10, balance_after=10, reason="admin_gift", created_at=now - timedelta(days=3), idempotency_key="gift-key"))
    s.add(WalletTransaction(user_id=u.id, wallet_id=w.id, type="credit", amount_coins=20, balance_after=30, reason="payment_topup", created_at=now - timedelta(days=2)))
    c = UsageCharge(idempotency_key="charge-key", user_id=u.id, wallet_id=w.id, feature="chat", provider="p", model="m", status="settled", charged_coins=5, refunded_coins=2, created_at=now - timedelta(days=1), refunded_at=now)
    s.add(c); s.add(PaymentReceipt(user_id=u.id, telegram_file_id="f", telegram_file_type="photo", status="approved", purpose="wallet_topup", approved_coins=20, created_at=now - timedelta(hours=1)))
    s.commit()
    rows = AdminFinancialLedgerService().rows(s, u.id, limit=20)
    assert rows == sorted(rows, key=lambda r: (r.time, r.linked_object), reverse=True)
    assert "administrative_gift" in {r.category for r in rows}
    assert "payment_receipt_approval" in {r.category for r in rows}
    assert any(r.category == "refund" and r.linked_object == f"usage_charge:{c.id}" for r in rows)
    assert s.query(WalletTransaction).count() == 2


def test_reconciliation_detects_mismatch_without_mutating_wallet():
    s = db(); u = User(telegram_id=2); s.add(u); s.flush(); w = Wallet(user_id=u.id, balance_coins=99); s.add(w); s.flush()
    s.add(WalletTransaction(user_id=u.id, wallet_id=w.id, type="credit", amount_coins=10, balance_after=10, reason="welcome", created_at=datetime.utcnow()))
    s.commit()
    rec = AdminFinancialLedgerService().reconciliation(s, u.id)
    assert rec["mismatch"] is True
    assert s.get(Wallet, w.id).balance_coins == 99
