from datetime import datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import LegacySubscriptionPreservation, Subscription, User, Wallet, WalletTransaction, UsageCharge
from app.models.settings import AppSetting
from app.models.usage import AiUsageEvent
from app.services.coin_pricing_service import CoinPricingService
from app.services.usage_billing_service import UsageBillingService


@pytest.fixture
def db():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, Subscription.__table__, LegacySubscriptionPreservation.__table__, UsageCharge.__table__, AppSetting.__table__, AiUsageEvent.__table__])
    S = sessionmaker(bind=e)
    s = S(); yield s; s.close()


def make_user(db, *, balance=100):
    u = User(telegram_id=1000 + (db.query(User).count() + 1))
    db.add(u); db.flush()
    db.add(Wallet(user_id=u.id, balance_coins=balance, total_added_coins=balance, total_spent_coins=0))
    db.flush(); return u


def preserve(db, u, *, expires_at=None, status="active", plan="vip", preservation_user=None):
    sub = Subscription(user_id=u.id, plan=plan, status=status, expires_at=expires_at or (datetime.utcnow() + timedelta(days=7)))
    db.add(sub); db.flush()
    db.add(LegacySubscriptionPreservation(subscription_id=sub.id, user_id=(preservation_user or u).id, plan=plan, status=status, expires_at=sub.expires_at, preservation_policy="preserve_until_expiry"))
    db.flush(); return sub


def quote(db, usd="0.01"):
    return CoinPricingService().quote_usd(db, Decimal(usd))


def reserve_settle(db, u, feature):
    svc = UsageBillingService(); q = quote(db)
    before = (u.wallet.balance_coins, u.wallet.total_spent_coins)
    ch = svc.reserve(db, user=u, idempotency_key=f"{feature}:{u.id}", feature=feature, provider="venice", model="m", quote=q)
    svc.settle(db, charge=ch, actual_quote=quote(db, "0.02"))
    return ch, before, (u.wallet.balance_coins, u.wallet.total_spent_coins)


def test_preserved_active_vip_chat_zero_charge_and_wallet_unchanged(db):
    u = make_user(db); sub = preserve(db, u)
    ch, before, after = reserve_settle(db, u, "chat")
    assert ch.id and ch.reserved_coins == 0 and ch.charged_coins == 0
    assert before == after
    assert ch.request_metadata_json["billing_exempt"] is True
    assert ch.request_metadata_json["legacy_subscription_id"] == sub.id
    assert db.query(WalletTransaction).count() == 0


@pytest.mark.parametrize("feature", ["stt", "vision", "tts"])
def test_preserved_subscription_exempts_stt_vision_tts_and_audits_cost(db, feature):
    u = make_user(db); preserve(db, u)
    ch, before, after = reserve_settle(db, u, feature)
    assert before == after
    assert ch.charged_coins == 0
    assert Decimal(ch.estimated_cost_usd) > 0 and Decimal(ch.actual_cost_usd) > 0


def test_image_generation_for_preserved_vip_remains_coin_billed(db):
    u = make_user(db); preserve(db, u)
    ch, before, after = reserve_settle(db, u, "image_generation")
    assert ch.charged_coins > 0 and after[0] < before[0] and after[1] > before[1]
    assert not (ch.request_metadata_json or {}).get("billing_exempt")


def test_expired_preserved_subscription_not_exempt(db):
    u = make_user(db); preserve(db, u, expires_at=datetime.utcnow() - timedelta(seconds=1))
    ch, before, after = reserve_settle(db, u, "chat")
    assert ch.charged_coins > 0 and after[0] < before[0]


def test_active_paid_subscription_without_preservation_row_not_exempt(db):
    u = make_user(db)
    db.add(Subscription(user_id=u.id, plan="vip", status="active", expires_at=datetime.utcnow() + timedelta(days=7))); db.flush()
    ch, before, after = reserve_settle(db, u, "chat")
    assert ch.charged_coins > 0 and after[0] < before[0]


def test_mismatched_preservation_user_subscription_not_exempt(db):
    u = make_user(db); other = make_user(db)
    preserve(db, u, preservation_user=other)
    ch, before, after = reserve_settle(db, u, "chat")
    assert ch.charged_coins > 0 and after[0] < before[0]


def test_provider_failure_for_exempt_usage_no_wallet_mutation(db):
    u = make_user(db); preserve(db, u); svc = UsageBillingService(); before = (u.wallet.balance_coins, u.wallet.total_spent_coins)
    ch = svc.reserve(db, user=u, idempotency_key="fail", feature="chat", provider="venice", model="m", quote=quote(db))
    svc.refund(db, charge=ch, error="provider failed")
    assert (u.wallet.balance_coins, u.wallet.total_spent_coins) == before
    assert ch.status == "refunded" and ch.error == "provider failed"


def test_retry_same_idempotency_key_no_duplicate_and_no_wallet_mutation(db):
    u = make_user(db); preserve(db, u); svc = UsageBillingService(); before = u.wallet.balance_coins
    ch1 = svc.reserve(db, user=u, idempotency_key="same-exempt", feature="chat", provider="venice", model="m", quote=quote(db))
    ch2 = svc.reserve(db, user=u, idempotency_key="same-exempt", feature="chat", provider="venice", model="m", quote=quote(db))
    assert ch1.id == ch2.id and db.query(UsageCharge).count() == 1 and u.wallet.balance_coins == before


def test_settlement_of_exempt_usage_records_actual_cost_and_keeps_zero(db):
    u = make_user(db); preserve(db, u); svc = UsageBillingService()
    ch = svc.reserve(db, user=u, idempotency_key="settle-exempt", feature="chat", provider="venice", model="m", quote=quote(db))
    event = AiUsageEvent(user_id=u.id, feature="chat", provider="venice", model="m", input_tokens=1, output_tokens=1, cost_usd=Decimal("0.02"))
    db.add(event); db.flush()
    svc.settle(db, charge=ch, actual_quote=quote(db, "0.02"), usage_event=event)
    assert ch.status == "settled" and ch.charged_coins == 0 and Decimal(ch.actual_cost_usd) > 0
    assert event.usage_charge_id == ch.id and event.charged_coins == 0
