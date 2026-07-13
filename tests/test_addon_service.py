from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.addon import AddonProduct, UserAddon
from app.services.addon_service import AddonService, ADULT_IMAGE_GENERATION_UNLOCK, HIGH_COMPLIANCE_COMPANION_MODE, seed_adult_image_generation_addon, seed_high_compliance_companion_mode_addon


def db():
    e = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, AddonProduct.__table__, UserAddon.__table__])
    return sessionmaker(bind=e)()


def user(s):
    u = User(telegram_id=10, display_name='u')
    s.add(u); s.commit(); return u


def test_purchase_creates_active_enabled_and_toggle_keeps_ownership():
    s=db(); u=user(s); svc=AddonService(); seed_adult_image_generation_addon(s)
    addon=svc.activate_addon_for_user(s, user_id=u.id, addon_key=ADULT_IMAGE_GENERATION_UNLOCK, source='wallet_purchase', price_paid_coins=0)
    assert addon.status == 'active' and addon.is_enabled is True
    assert svc.user_owns_addon(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
    svc.set_user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK, False)
    assert svc.user_owns_addon(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
    assert not svc.user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
    before = addon.price_paid_coins
    svc.set_user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK, True)
    assert svc.user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
    assert addon.price_paid_coins == before


def test_toggle_requires_ownership():
    s=db(); u=user(s); svc=AddonService()
    try:
        svc.set_user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK, True)
        assert False
    except ValueError as exc:
        assert str(exc) == 'addon_not_owned'


def test_high_compliance_purchase_starts_enabled_and_toggles_without_payment():
    s=db(); u=user(s); svc=AddonService(); seed_high_compliance_companion_mode_addon(s)
    addon=svc.activate_addon_for_user(s, user_id=u.id, addon_key=HIGH_COMPLIANCE_COMPANION_MODE, source='wallet_purchase', price_paid_coins=0)
    assert addon.status == 'active'
    assert addon.is_enabled is True
    assert svc.user_owns_addon(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE)
    svc.set_user_addon_enabled(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE, False)
    assert svc.user_owns_addon(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE)
    assert not svc.user_addon_enabled(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE)
    before = addon.price_paid_coins
    svc.set_user_addon_enabled(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE, True)
    assert svc.user_addon_enabled(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE)
    assert addon.price_paid_coins == before


def test_toggling_one_addon_does_not_toggle_other_addon():
    s=db(); u=user(s); svc=AddonService(); seed_adult_image_generation_addon(s); seed_high_compliance_companion_mode_addon(s)
    svc.activate_addon_for_user(s, user_id=u.id, addon_key=ADULT_IMAGE_GENERATION_UNLOCK, source='wallet_purchase', price_paid_coins=0)
    svc.activate_addon_for_user(s, user_id=u.id, addon_key=HIGH_COMPLIANCE_COMPANION_MODE, source='wallet_purchase', price_paid_coins=0)
    svc.set_user_addon_enabled(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE, False)
    assert not svc.user_addon_enabled(s, u.id, HIGH_COMPLIANCE_COMPANION_MODE)
    assert svc.user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
