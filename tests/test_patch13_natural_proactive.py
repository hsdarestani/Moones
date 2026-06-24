from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.message import Message
from app.models.proactive import ProactiveMessage
from app.models.settings import AppSetting
from app.models.subscription import Subscription
from app.models.user import User
from app.services.proactive_service import PROACTIVE_INTENT_WEIGHTS, TEMPLATES, ProactiveService
from app.services.settings_service import DEFAULT_SETTINGS


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for key, (value, typ, desc) in DEFAULT_SETTINGS.items():
        db.add(AppSetting(key=key, value=value, value_type=typ, description=desc))
    db.commit()
    return db


def add_user(db, **kw):
    u = User(telegram_id=kw.pop("telegram_id", 900), onboarding_step="complete", last_seen_at=datetime(2026, 6, 23, 12), **kw)
    db.add(u); db.flush()
    db.add(Subscription(user_id=u.id, plan="free", status="active", starts_at=datetime(2026, 6, 1)))
    db.commit(); return u


def test_send_window_blocks_0500_2350_and_allows_1500():
    db = make_db(); svc = ProactiveService(); u = add_user(db, next_proactive_at=datetime(2026, 6, 24, 5))
    assert svc.eligible_users(db, now=datetime(2026, 6, 24, 5)) == []
    assert u.next_proactive_at.hour in {10, 11, 12}
    u.next_proactive_at = datetime(2026, 6, 24, 23, 50); db.commit()
    assert svc.eligible_users(db, now=datetime(2026, 6, 24, 23, 50)) == []
    u.next_proactive_at = datetime(2026, 6, 24, 15); db.commit()
    assert svc.eligible_users(db, now=datetime(2026, 6, 24, 15)) == [u]


def test_daily_max_default_two_and_third_skipped_next_day_resets():
    db = make_db(); svc = ProactiveService(); u = add_user(db)
    day = datetime(2026, 6, 24, 15)
    db.add_all([ProactiveMessage(user_id=u.id, text="a", status="sent", sent_at=day), ProactiveMessage(user_id=u.id, text="b", status="sent", sent_at=day + timedelta(hours=1))]); db.commit()
    assert svc.skip_reason(db, u, day + timedelta(hours=2)) == "daily_max"
    assert svc.skip_reason(db, u, day + timedelta(days=1)) is None


def test_intents_and_templates_are_non_question_dominant():
    assert PROACTIVE_INTENT_WEIGHTS["simple_checkin"] <= 5
    endings = [t.strip().endswith(("؟", "?")) for values in TEMPLATES.values() for t in values]
    assert endings.count(False) / len(endings) >= 0.7


def test_question_guard_softens_after_two_questions():
    db = make_db(); svc = ProactiveService(); u = add_user(db)
    db.add_all([Message(user_id=u.id, role="assistant", content="خوبی؟"), Message(user_id=u.id, role="assistant", content="کجایی؟")]); db.commit()
    text = svc.soften_question_ending(db, u, "می‌خوای حرف بزنیم؟", context="chat")
    assert not text.endswith(("؟", "?"))


def test_stop_phrases_disable_skip_reason():
    db = make_db(); svc = ProactiveService(); u = add_user(db)
    db.add(Message(user_id=u.id, role="user", content="لطفا پیام نده")); db.commit()
    assert svc.skip_reason(db, u, datetime(2026, 6, 24, 15)) == "user_asked_stop"
