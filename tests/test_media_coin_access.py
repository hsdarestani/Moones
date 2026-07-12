from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import User, Wallet
from app.models.subscription import DailyUsage
from app.models.settings import AppSetting
from app.services.media_input_service import MediaInputService


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Wallet.__table__, DailyUsage.__table__, AppSetting.__table__])
    session = sessionmaker(bind=engine)()
    return session


def _user(db):
    u = User(telegram_id=4242)
    db.add(u); db.flush()
    db.add(Wallet(user_id=u.id, balance_coins=100, total_added_coins=100, total_spent_coins=0))
    db.flush()
    return u


def test_normal_user_with_coins_can_use_stt_and_vision_without_paid_plan(monkeypatch):
    db = _db(); u = _user(db)
    monkeypatch.setenv("FREE_PLAN_MEDIA_ENABLED", "false")
    svc = MediaInputService()
    assert svc.can_use_media(db, u, "voice") == (True, None)
    assert svc.can_use_media(db, u, "photo") == (True, None)


def test_media_counters_do_not_block_usage(monkeypatch):
    db = _db(); u = _user(db)
    usage = DailyUsage(user_id=u.id, date=date.today(), monthly_image_inputs_used=9999, monthly_voice_inputs_used=9999)
    db.add(usage); db.flush()
    db.add(AppSetting(key="basic_monthly_image_inputs", value="1", value_type="integer"))
    db.add(AppSetting(key="basic_monthly_voice_inputs", value="1", value_type="integer"))
    db.flush()
    svc = MediaInputService()
    assert svc.can_use_media(db, u, "photo") == (True, None)
    assert svc.can_use_media(db, u, "voice") == (True, None)
