import asyncio
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.telegram import _handle_callback
from app.db.base import Base
from app.models import AppSetting, Subscription, User, Wallet, WalletTransaction
from app.services.onboarding_service import OnboardingService
from app.services.wallet_service import ensure_signup_welcome_credit
from scripts.backfill_signup_welcome_credit import eligible_users


def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AppSetting.__table__, Subscription.__table__])
    db = sessionmaker(bind=engine)()
    db.add(AppSetting(key="billing.signup_bonus_coins", value="200", value_type="integer"))
    db.flush()
    return db


def complete_user(db, telegram_id=100, amount=None):
    if amount is not None:
        db.query(AppSetting).filter_by(key="billing.signup_bonus_coins").one().value = str(amount)
    svc = OnboardingService()
    user = svc.get_or_create_user(db, telegram_id, "tester")
    svc.handle_callback(user, "onboard_start")
    svc.handle_callback(user, "onboard_gender:female")
    svc.handle_text(user, "سارا")
    svc.handle_callback(user, "onboard_age:21-25")
    svc.handle_callback(user, "onboard_personality:calm_caring")
    return user


async def finish_async(db, user):
    await _handle_callback(db, user, "onboard_interest:music", user.telegram_id, "management")
    return await _handle_callback(db, user, "onboard_done_interests", user.telegram_id, "management")

def finish(db, user):
    return asyncio.run(finish_async(db, user))

def callback(db, user, data):
    return asyncio.run(_handle_callback(db, user, data, user.telegram_id, "management"))

def chat_callback(db, user, data):
    return asyncio.run(_handle_callback(db, user, data, user.telegram_id, "chat"))


def test_new_management_user_receives_configured_bonus():
    db = session(); user = complete_user(db, amount=321)
    text, _ = finish(db, user)
    tx = db.query(WalletTransaction).filter_by(user_id=user.id, reason="signup_welcome_credit").one()
    assert user.wallet.balance_coins == 321
    assert tx.amount_coins == 321 and tx.idempotency_key == f"welcome:{user.id}"
    assert "۳۲۱ سکه هدیه شروع" in text


def test_chat_bot_first_then_management_completion_receives_bonus():
    db = session(); svc = OnboardingService()
    user = svc.get_or_create_user(db, 101, "chat-first")
    assert user.wallet is not None and user.wallet.balance_coins == 0
    user = complete_user(db, telegram_id=101)
    finish(db, user)
    assert user.wallet.balance_coins == 200


def test_duplicate_completion_callback_grants_once():
    db = session(); user = complete_user(db)
    finish(db, user)
    text, _ = finish(db, user)
    assert db.query(WalletTransaction).filter_by(reason="signup_welcome_credit").count() == 1
    assert user.wallet.balance_coins == 200
    assert "هدیه شروع" not in text


def test_editing_partner_after_completion_does_not_grant_again():
    db = session(); user = complete_user(db)
    finish(db, user)
    OnboardingService().reset_for_edit(user)
    user.onboarding_step = "complete"
    callback(db, user, "onboard_done")
    assert db.query(WalletTransaction).filter_by(reason="signup_welcome_credit").count() == 1
    assert user.wallet.balance_coins == 200


def test_existing_transaction_null_marker_blocks_duplicate():
    db = session(); user = complete_user(db)
    wallet = user.wallet
    db.add(WalletTransaction(user_id=user.id, wallet_id=wallet.id, type="credit", amount_coins=200, balance_after=200, reason="signup_welcome_credit", unit="coin"))
    text, _ = finish(db, user)
    assert db.query(WalletTransaction).filter_by(reason="signup_welcome_credit").count() == 1
    assert user.wallet.balance_coins == 0
    assert user.welcome_coins_granted_at is not None
    assert user.welcome_coins_amount == 200
    assert "هدیه شروع" not in text


def test_marker_without_transaction_blocks_duplicate():
    db = session(); user = complete_user(db)
    user.welcome_coins_granted_at = datetime.utcnow()
    text, _ = finish(db, user)
    assert db.query(WalletTransaction).filter_by(reason="signup_welcome_credit").count() == 0
    assert user.wallet.balance_coins == 0
    assert "هدیه شروع" not in text


def test_backfill_filters_completed_missing_users_only():
    db = session()
    eligible = User(telegram_id=1, onboarding_step="complete")
    incomplete = User(telegram_id=2, onboarding_step="interests")
    marked = User(telegram_id=3, onboarding_step="complete", welcome_coins_granted_at=datetime.utcnow())
    with_tx = User(telegram_id=4, onboarding_step="complete")
    db.add_all([eligible, incomplete, marked, with_tx]); db.flush()
    db.add_all([Wallet(user_id=u.id) for u in [eligible, incomplete, marked, with_tx]]); db.flush()
    db.add(WalletTransaction(user_id=with_tx.id, wallet_id=with_tx.wallet.id, type="credit", amount_coins=200, balance_after=200, reason="signup_welcome_credit", unit="coin")); db.flush()
    assert [u.id for u in eligible_users(db, None)] == [eligible.id]


def test_management_then_chat_remains_once():
    db = session(); user = complete_user(db, telegram_id=202)
    ensure_signup_welcome_credit(db, user=user, source="management_start")
    ensure_signup_welcome_credit(db, user=user, source="chat_start")
    assert user.wallet.balance_coins == 200
    assert db.query(WalletTransaction).filter_by(idempotency_key=f"welcome:{user.id}").count() == 1


def test_chat_then_management_remains_once():
    db = session(); user = complete_user(db, telegram_id=203)
    ensure_signup_welcome_credit(db, user=user, source="chat_start")
    ensure_signup_welcome_credit(db, user=user, source="management_start")
    assert user.wallet.balance_coins == 200
    assert db.query(WalletTransaction).filter_by(reason="signup_welcome_credit").count() == 1


def test_correctly_granted_user_unchanged_and_legacy_identifiers_recognized():
    db = session(); user = complete_user(db, telegram_id=204)
    first = ensure_signup_welcome_credit(db, user=user, source="management_start")
    second = ensure_signup_welcome_credit(db, user=user, source="chat_start")
    assert first.status == "granted"
    assert second.status == "already_granted"
    tx = db.query(WalletTransaction).filter_by(user_id=user.id).one()
    assert tx.idempotency_key == f"welcome:{user.id}"
    assert tx.reason == "signup_welcome_credit"
    assert user.wallet.balance_coins == 200
