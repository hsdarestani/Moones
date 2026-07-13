from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.addon import AddonProduct, UserAddon
from app.services.bot_menu_service import BotMenuService
from app.services.addon_service import ADULT_IMAGE_GENERATION_UNLOCK, seed_adult_image_generation_addon


def db():
    e = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, AddonProduct.__table__, UserAddon.__table__])
    return sessionmaker(bind=e, expire_on_commit=False)()


def test_management_toggle_callbacks_require_ownership():
    s=db(); u=User(telegram_id=1); s.add(u); s.commit(); seed_adult_image_generation_addon(s)
    msg, _ = BotMenuService().toggle_addon(s, u, ADULT_IMAGE_GENERATION_UNLOCK, True)
    assert 'اول باید افزودنی رو خریداری کنی' in msg


def test_management_toggle_changes_enabled_state_without_removing_ownership():
    s=db(); u=User(telegram_id=1); s.add(u); s.commit(); seed_adult_image_generation_addon(s)
    s.add(UserAddon(user_id=u.id, addon_key=ADULT_IMAGE_GENERATION_UNLOCK, status='active', is_enabled=True)); s.commit()
    svc=BotMenuService(); msg, _ = svc.toggle_addon(s, u, ADULT_IMAGE_GENERATION_UNLOCK, False)
    assert 'مالکیتت حذف نشده' in msg
    assert svc.addons.user_owns_addon(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
    assert not svc.addons.user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
    msg, _ = svc.toggle_addon(s, u, ADULT_IMAGE_GENERATION_UNLOCK, True)
    assert 'بدون خرید دوباره' in msg
    assert svc.addons.user_addon_enabled(s, u.id, ADULT_IMAGE_GENERATION_UNLOCK)
