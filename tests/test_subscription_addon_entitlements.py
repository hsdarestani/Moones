from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.addon import AddonProduct, UserAddon
from app.models.relationship import Relationship
from app.models.subscription import DailyUsage, Subscription
from app.models.user import User
from app.services.addon_service import AddonService, INTIMACY_MAX_UNLOCK
from app.services.subscription_service import SubscriptionService


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Subscription.__table__, DailyUsage.__table__, Relationship.__table__, AddonProduct.__table__, UserAddon.__table__])
    return sessionmaker(bind=engine)()


def make_user(db, telegram_id=1001):
    user = User(telegram_id=telegram_id, onboarding_step="done")
    db.add(user)
    db.flush()
    return user


def test_same_active_paid_plan_quote_returns_renewal_and_renew_extends_from_current_expiry():
    db = make_db(); user = make_user(db)
    now = datetime.utcnow()
    current_expiry = now + timedelta(days=10)
    db.add(Subscription(user_id=user.id, plan="plus", status="active", starts_at=now - timedelta(days=20), expires_at=current_expiry))
    db.flush()

    service = SubscriptionService()
    quote = service.quote_upgrade(db, user, "plus", now=now)

    assert quote["renewal"] is True
    assert quote["upgrade"] is False
    assert quote["amount"] == 2_290_000
    assert quote.get("reason") is None
    assert quote["metadata"]["payment_type"] == "subscription_renewal"
    assert quote["new_expires_at"] == current_expiry + timedelta(days=30)

    sub = service.renew_plan(db, user, "plus", now=now)
    assert sub.status == "active"
    assert sub.expires_at == current_expiry + timedelta(days=30)


def test_upgrade_keeps_previous_expiry_and_charges_prorated_amount():
    db = make_db(); user = make_user(db)
    now = datetime.utcnow()
    expiry = now + timedelta(days=15)
    db.add(Subscription(user_id=user.id, plan="plus", status="active", starts_at=now - timedelta(days=15), expires_at=expiry))
    db.flush()

    service = SubscriptionService()
    quote = service.quote_upgrade(db, user, "vip", now=now)

    assert quote["upgrade"] is True
    assert not quote.get("renewal", False)
    assert quote["expires_at"] == expiry
    assert quote["amount"] == 1_305_000

    sub = service.apply_prorated_upgrade(db, user, "vip", quote["expires_at"])
    assert sub.plan == "vip"
    assert sub.expires_at == expiry


def test_lower_paid_plan_remains_blocked():
    db = make_db(); user = make_user(db)
    now = datetime.utcnow()
    db.add(Subscription(user_id=user.id, plan="plus", status="active", starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=29)))
    db.flush()

    quote = SubscriptionService().quote_upgrade(db, user, "basic", now=now)

    assert quote["upgrade"] is False
    assert quote["renewal"] is False
    assert quote["reason"] in {"lower_plan", "same_or_lower"}
    assert quote["amount"] == 0


def test_user_has_addon_expires_past_timed_addon_and_keeps_lifetime_active():
    db = make_db(); user = make_user(db)
    now = datetime.utcnow()
    timed = UserAddon(user_id=user.id, addon_key="timed", status="active", expires_at=now - timedelta(seconds=1))
    lifetime = UserAddon(user_id=user.id, addon_key=INTIMACY_MAX_UNLOCK, status="active", expires_at=None)
    db.add_all([timed, lifetime]); db.flush()

    service = AddonService()
    assert service.user_has_addon(db, user.id, "timed") is False
    assert timed.status == "expired"
    assert service.user_has_addon(db, user.id, INTIMACY_MAX_UNLOCK) is True


def test_activate_addon_supports_duration_metadata_and_lifetime_default():
    db = make_db(); user = make_user(db)
    db.add(AddonProduct(key="timed", title="Timed", price_toman=1, metadata_json={"duration_days": 7}))
    db.add(AddonProduct(key=INTIMACY_MAX_UNLOCK, title="Lifetime", price_toman=1, metadata_json={}))
    db.flush()

    service = AddonService()
    first = service.activate_addon_for_user(db, user_id=user.id, addon_key="timed")
    first_expiry = first.expires_at
    assert first.status == "active"
    assert first_expiry is not None

    second = service.activate_addon_for_user(db, user_id=user.id, addon_key="timed")
    assert second.expires_at > first_expiry

    lifetime = service.activate_addon_for_user(db, user_id=user.id, addon_key=INTIMACY_MAX_UNLOCK)
    assert lifetime.expires_at is None
