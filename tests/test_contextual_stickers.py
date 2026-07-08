from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.relationship import Relationship
from app.models.sticker import StickerItem
from app.models.subscription import DailyUsage
from app.models.user import User
from app.services.sticker_service import StickerService


def make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Relationship.__table__, StickerItem.__table__, DailyUsage.__table__])
    return sessionmaker(bind=engine)()


def user(db, gender="female", stage="LOVER", adult=True):
    u = User(telegram_id=123, partner_gender=gender, partner_age_range="21-25", mature_intimacy_unlocked=adult, intimacy_override_max=adult, onboarding_step="complete")
    db.add(u); db.flush()
    rel = Relationship(user_id=u.id, stage=stage, intimacy=.9, trust=.9, attachment=.9, attraction=.9)
    db.add(rel); u.relationship_state = rel; db.flush()
    return u


def add_sticker(db, **kw):
    item = StickerItem(
        telegram_file_id=kw.pop("telegram_file_id", "file"),
        label=kw.pop("label", "sticker"),
        usage_context=kw.pop("usage_context", "comfort"),
        category=kw.pop("category", "normal"),
        gender_target=kw.pop("gender_target", "neutral"),
        enabled=kw.pop("enabled", True),
        is_active=kw.pop("is_active", True),
        probability=kw.pop("probability", 1),
        **kw,
    )
    db.add(item); db.flush()
    return item


def test_adult_sticker_blocked_in_normal_chat():
    db = make_db(); u = user(db, adult=True)
    add_sticker(db, telegram_file_id="adult", category="adult_intimacy", gender_target="female")
    assert StickerService().select_contextual_sticker(db, u, {"adult_chat_mode": False}, "playful", "adult_intimacy") is None


def test_adult_sticker_allowed_in_adult_mode():
    db = make_db(); u = user(db, adult=True)
    item = add_sticker(db, telegram_file_id="adult", category="adult_intimacy", gender_target="female", relationship_stages=["LOVER"])
    selected = StickerService().select_contextual_sticker(db, u, {"adult_chat_mode": True}, "playful", "adult_intimacy")
    assert selected.id == item.id


def test_partner_gender_target_filtering_uses_ai_partner_gender():
    db = make_db(); u = user(db, gender="male")
    add_sticker(db, telegram_file_id="female", category="playful", gender_target="female")
    male = add_sticker(db, telegram_file_id="male", category="playful", gender_target="male")
    selected = StickerService().select_contextual_sticker(db, u, {"mood": "playful"}, "playful", "playful")
    assert selected.id == male.id


def test_emoji_metadata_selection_is_preferred():
    db = make_db(); u = user(db)
    add_sticker(db, telegram_file_id="plain", category="playful", gender_target="female", mood="playful")
    emoji = add_sticker(db, telegram_file_id="emoji", category="playful", gender_target="female", trigger_emojis=["😉"], mood="warm")
    selected = StickerService().select_contextual_sticker(db, u, {"text": "باشه 😉", "mood": "warm"}, "warm", "playful")
    assert selected.id == emoji.id


def test_admin_can_create_unlimited_sticker_records_model_has_no_fixed_limit():
    db = make_db()
    for i in range(12):
        add_sticker(db, telegram_file_id=f"file-{i}", key=f"k{i}")
    assert db.query(StickerItem).count() == 12


def test_daily_limit_enforced():
    db = make_db(); u = user(db)
    add_sticker(db, telegram_file_id="capped", category="normal", daily_limit=1)
    db.add(DailyUsage(user_id=u.id, date=date.today(), daily_stickers_sent=1)); db.flush()
    assert StickerService().select_contextual_sticker(db, u, {}, "comfort", "normal") is None
