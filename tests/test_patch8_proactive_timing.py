from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.settings import AppSetting
from app.models.subscription import Subscription
from app.models.user import User
from app.services.proactive_service import ProactiveService
from app.services.settings_service import DEFAULT_SETTINGS


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for key, (value, typ, desc) in DEFAULT_SETTINGS.items():
        db.add(AppSetting(key=key, value=value, value_type=typ, description=desc))
    db.commit()
    set_setting(db, "proactive.enabled", "true", "boolean")
    set_setting(db, "proactive.quiet_hours_start", "00:00")
    set_setting(db, "proactive.quiet_hours_end", "00:01")
    return db


def set_setting(db, key, value, typ="string"):
    row = db.query(AppSetting).filter_by(key=key).first()
    if row:
        row.value = str(value); row.value_type = typ
    else:
        db.add(AppSetting(key=key, value=str(value), value_type=typ))
    db.commit()


def user(db, plan="free", **kw):
    u = User(telegram_id=kw.pop("telegram_id", 100), onboarding_step="complete", last_seen_at=kw.pop("last_seen_at", datetime.utcnow() - timedelta(days=2)), **kw)
    db.add(u); db.flush()
    db.add(Subscription(user_id=u.id, plan=plan, status="active", starts_at=datetime.utcnow()))
    db.commit()
    return u


def test_null_next_proactive_schedules_first_time_without_selection(caplog):
    db = make_db(); u = user(db, next_proactive_at=None, last_seen_at=datetime(2026, 6, 18, 12, 0, 0))
    caplog.set_level("INFO")
    selected = ProactiveService().eligible_users(db, now=datetime(2026, 6, 19, 15, 0, 0), limit=10)
    assert selected == []
    assert u.next_proactive_at is not None
    assert "PROACTIVE_NEXT_SCHEDULED" in caplog.text
    assert "scheduled_first_time" in caplog.text


def test_future_next_proactive_not_due(caplog):
    db = make_db(); u = user(db, next_proactive_at=datetime(2026, 6, 19, 17, 0, 0), last_seen_at=datetime(2026, 6, 18, 14, 0, 0))
    caplog.set_level("DEBUG")
    assert ProactiveService().eligible_users(db, now=datetime(2026, 6, 19, 15, 0, 0)) == []
    assert "not_due_yet" in caplog.text


def test_past_next_proactive_is_eligible():
    db = make_db(); now = datetime(2026, 6, 19, 15, 0, 0); u = user(db, next_proactive_at=now - timedelta(minutes=1))
    assert ProactiveService().eligible_users(db, now=now) == [u]


def test_send_one_success_reschedules_future():
    class Telegram:
        async def send_text(self, chat_id, text):
            return None
    db = make_db(); set_setting(db, "proactive.send_window_start", "00:00"); set_setting(db, "proactive.send_window_end", "23:59"); u = user(db, next_proactive_at=datetime.utcnow() - timedelta(minutes=1))
    before = datetime.utcnow()
    assert asyncio.run(ProactiveService().send_one(db, u, svc=Telegram())) is True
    assert u.next_proactive_at > before


def test_user_activity_reschedules_directly():
    db = make_db(); u = user(db, next_proactive_at=datetime.utcnow() - timedelta(minutes=1))
    now = datetime.utcnow()
    ProactiveService().schedule_next_proactive(db, u, now, reason="user_activity")
    assert u.next_proactive_at > now + timedelta(hours=17)


def test_plus_vip_ranges_shorter_than_free_basic_and_randomized():
    db = make_db(); svc = ProactiveService()
    assert svc.plan_random_hours(db, "plus")[1] < svc.plan_random_hours(db, "free")[0]
    assert svc.plan_random_hours(db, "vip")[1] <= svc.plan_random_hours(db, "basic")[0]
    u = user(db, plan="plus")
    now = datetime.utcnow()
    first = svc.schedule_next_proactive(db, u, now, reason="test")
    second = svc.schedule_next_proactive(db, u, now, reason="test")
    assert first != second


def test_quiet_hours_prevent_sending_and_reschedule_due():
    db = make_db(); set_setting(db, "proactive.send_window_start", "23:59"); set_setting(db, "proactive.send_window_end", "00:00")
    now = datetime(2026, 6, 19, 12, 0, 0)
    u = user(db, next_proactive_at=now - timedelta(minutes=1))
    assert ProactiveService().eligible_users(db, now=now) == []
    assert u.next_proactive_at > now


def test_scheduler_startup_logs_and_uses_short_initial_sleep():
    text = open("app/main.py", encoding="utf-8").read()
    assert "PROACTIVE_SCHEDULER_STARTED" in text
    assert "random.randint(5, 15)" in text
    assert "await asyncio.sleep(900)" not in text


def test_manual_script_exists_and_supports_user_id():
    text = open("scripts/send_proactive_test.py", encoding="utf-8").read()
    assert "--user-id" in text and "bypass_schedule=True" in text


def test_user_facing_texts_do_not_expose_exact_proactive_intervals():
    from app.services.bot_menu_service import BotMenuService
    text = BotMenuService().settings_text()
    assert "۳ تا ۹ ساعت" not in text
    assert "3" not in text and "9" not in text
