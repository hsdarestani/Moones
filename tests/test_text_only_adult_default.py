from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.engine.delivery_decider import decide_delivery
from app.models.addon import AddonProduct, UserAddon
from app.models.relationship import Relationship
from app.models.user import User
from app.services.addon_service import (
    ADULT_IMAGE_GENERATION_UNLOCK,
    HIGH_COMPLIANCE_COMPANION_MODE,
    IMAGE_GENERATION_UNLOCK,
    INTIMACY_MAX_UNLOCK,
    AddonService,
)
from app.services.media_input_service import MediaInputService
from app.services.partner_style import build_partner_style_dna, format_partner_style_sections


@pytest.fixture
def product_mode(monkeypatch):
    values = {
        "TEXT_ONLY_MODE": "true",
        "ADULT_CHAT_DEFAULT": "true",
        "ADULT_CHAT_MAX_INTIMACY": "true",
        "ALLOW_EXPLICIT_CONTENT": "true",
        "IMAGE_INPUT_ENABLED": "false",
        "IMAGE_GENERATION_ENABLED": "false",
        "VOICE_INPUT_ENABLED": "false",
        "VENICE_TTS_ENABLED": "false",
        "VENICE_MODEL": "qwen-3-6-plus",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, AddonProduct.__table__, UserAddon.__table__])
    return sessionmaker(bind=engine)()


def test_product_mode_defaults_are_text_only_and_keep_qwen(product_mode):
    assert product_mode.text_only_mode is True
    assert product_mode.adult_chat_default is True
    assert product_mode.adult_chat_max_intimacy is True
    assert product_mode.venice_model == "qwen-3-6-plus"
    assert product_mode.image_input_enabled is False
    assert product_mode.image_generation_enabled is False
    assert product_mode.voice_input_enabled is False
    assert product_mode.venice_tts_enabled is False


def test_adult_profile_starts_at_max_intimacy(product_mode):
    user = SimpleNamespace(
        id=1,
        partner_name="مهناز",
        partner_gender="دختر",
        partner_age_range="25",
        partner_personality_type="رمانتیک",
        partner_interests="",
        partner_bio="",
        persona_style="",
        current_mood="warm",
    )
    relationship = Relationship(user_id=1, stage="STRANGER", intimacy=0.05, trust=0.05, attachment=0.05, attraction=0.03)
    dna = build_partner_style_dna(user, relationship, [])
    assert dna["adult_first"] is True
    assert dna["stage"] == "LOVER"
    assert dna["intimacy"] == 1.0
    prompt = format_partner_style_sections(dna)
    assert "never use warm-up or early-stage gating" in prompt
    assert "هنوز زوده" in prompt


def test_underage_profile_does_not_receive_adult_default(product_mode):
    user = SimpleNamespace(
        id=2,
        partner_name="x",
        partner_gender="x",
        partner_age_range="زیر ۱۸",
        partner_personality_type="natural",
        partner_interests="",
        partner_bio="",
        persona_style="",
        current_mood="warm",
    )
    relationship = Relationship(user_id=2, stage="STRANGER", intimacy=0.05, trust=0.05, attachment=0.05, attraction=0.03)
    dna = build_partner_style_dna(user, relationship, [])
    assert dna["adult_first"] is False
    assert dna["stage"] == "STRANGER"


def test_adult_chat_addons_are_automatic_but_image_addons_are_off(product_mode):
    session = _db()
    user = User(telegram_id=10, display_name="u", partner_age_range="25")
    session.add(user)
    session.commit()
    service = AddonService()
    assert service.user_has_addon(session, user.id, INTIMACY_MAX_UNLOCK)
    assert service.user_addon_enabled(session, user.id, HIGH_COMPLIANCE_COMPANION_MODE)
    assert not service.user_has_addon(session, user.id, IMAGE_GENERATION_UNLOCK)
    assert not service.user_addon_enabled(session, user.id, ADULT_IMAGE_GENERATION_UNLOCK)
    assert service.list_active_addons(session) == []


def test_photo_and_voice_inputs_are_rejected_in_product_mode(product_mode):
    service = MediaInputService()
    user = SimpleNamespace(id=1)
    photo_allowed, photo_message = service.can_use_media(None, user, "photo")
    voice_allowed, voice_message = service.can_use_media(None, user, "voice")
    assert photo_allowed is False and "عکس" in photo_message
    assert voice_allowed is False and "وویس" in voice_message


def test_delivery_is_always_plain_text_in_product_mode(product_mode):
    user = SimpleNamespace(current_mood="affectionate")
    decision = decide_delivery(user, "یه وویس و استیکر بفرست", "باشه عزیزم")
    assert decision.delivery_type == "text"
    assert decision.voice_probability == 0
    assert decision.sticker_probability == 0
    assert decision.sticker_file_id is None
