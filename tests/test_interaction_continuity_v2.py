from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.engine.simple_chat import _format_recent_messages, _load_recent_messages
from app.llm.tts_client import select_tts_voice
from app.models.image_generation import GeneratedVoiceOutput
from app.models.message import Message
from app.models.proactive import ProactiveMessage
from app.models.settings import AppSetting
from app.models.subscription import Subscription
from app.models.user import User
from app.services.conversation_time_service import ConversationTimeService
from app.services.forward_batch_service import compact_forward_item, format_forward_batch, is_forwarded_message
from app.services.generated_voice_service import capture_voice_feedback, load_voice_feedback_profile
from app.services.proactive_policy import ProactiveCandidate
from app.services.proactive_service import ProactiveService


TEST_TABLES = [
    User.__table__,
    AppSetting.__table__,
    Subscription.__table__,
    Message.__table__,
    ProactiveMessage.__table__,
    GeneratedVoiceOutput.__table__,
]


def db_user():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=TEST_TABLES,
    )
    db = sessionmaker(bind=engine)()
    user = User(
        telegram_id=101,
        onboarding_step="complete",
        proactive_messages_enabled=True,
        last_seen_at=datetime.utcnow() - timedelta(days=2),
        next_proactive_at=datetime.utcnow() - timedelta(minutes=1),
        partner_gender="male",
    )
    assert user.onboarding_complete is True
    db.add(user); db.flush()
    db.add(Subscription(user_id=user.id, plan="free", status="active", starts_at=datetime.utcnow()))
    db.commit()
    return db, user


def test_proactive_delivery_persists_linked_conversation_once(monkeypatch):
    db, user = db_user(); service = ProactiveService()
    async def candidate(*args): return ProactiveCandidate("حالت چطوره؟", "simple_checkin", confidence=.9)
    async def send(*args, **kwargs): return 7788
    monkeypatch.setattr(service, "generate_proactive_text", candidate)
    monkeypatch.setattr(service, "skip_reason", lambda *args: None)
    assert asyncio.run(service.send_one(db, user, svc=SimpleNamespace(send_text=send), force=True))
    row = db.scalar(select(ProactiveMessage)); messages = db.scalars(select(Message)).all()
    assert len(messages) == 1
    message = messages[0]
    assert message.content == "حالت چطوره؟" and message.telegram_message_id == 7788
    assert message.input_type == "proactive_text" and message.metadata_json["source"] == "proactive"
    assert row.extra_metadata == {**row.extra_metadata, "telegram_message_id": 7788,
                                  "conversation_message_id": message.id,
                                  "persisted_to_conversation": True}
    # Re-linking the same delivery identity is idempotent.
    row.extra_metadata = {**row.extra_metadata}; db.flush()
    assert len(_load_recent_messages(db, user.id)) == 1
    formatted = _format_recent_messages(_load_recent_messages(db, user.id))
    assert "assistant [proactive outreach]: حالت چطوره؟" in formatted
    context = ConversationTimeService().build_context(db, user, utc_now=datetime.utcnow())
    assert context.previous_assistant_message_at is not None and context.previous_user_message_at is None


def test_failed_proactive_delivery_does_not_create_message(monkeypatch):
    db, user = db_user(); service = ProactiveService()
    async def candidate(*args): return ProactiveCandidate("حالت چطوره؟", "simple_checkin", confidence=.9)
    async def send(*args, **kwargs):
        request = httpx.Request("POST", "https://telegram.invalid")
        raise httpx.HTTPStatusError("failed", request=request, response=httpx.Response(500, request=request))
    monkeypatch.setattr(service, "generate_proactive_text", candidate)
    monkeypatch.setattr(service, "skip_reason", lambda *args: None)
    assert not asyncio.run(service.send_one(db, user, svc=SimpleNamespace(send_text=send), force=True))
    assert db.scalar(select(Message)) is None


def test_forward_metadata_detection_and_safe_ordered_formatting():
    ordinary = SimpleNamespace(text="Forwarded words only", caption=None, message_id=3,
        forward_origin=None, forward_from=None, forward_from_chat=None, forward_sender_name=None, forward_date=None,
        photo=None, voice=None, audio=None, sticker=None, document=None)
    assert not is_forwarded_message(ordinary)
    first = SimpleNamespace(**{**ordinary.__dict__, "message_id": 8, "text": "اول", "forward_date": 1})
    second = SimpleNamespace(**{**ordinary.__dict__, "message_id": 9, "text": None, "caption": "دوم", "photo": [object()], "forward_origin": {"type": "user"}})
    assert is_forwarded_message(first) and is_forwarded_message(second)
    text = format_forward_batch([compact_forward_item(second, 12), compact_forward_item(first, 11)])
    assert text.index("اول") < text.index("دوم") and "[photo]" in text
    assert len(text) <= 6000


def test_voice_feedback_is_durable_scoped_and_changes_real_selector(monkeypatch):
    db, user = db_user(); now = datetime.utcnow()
    voice = GeneratedVoiceOutput(idempotency_key="v1", user_id=user.id, chat_id=101, status="sent",
        user_telegram_message_id=55, sent_at=now, created_at=now, metadata_json={})
    db.add(voice); db.commit()
    event = capture_voice_feedback(db, user_id=user.id, text="بازیگوش‌تر باش", source_message_id=60,
                                   reply_to_message_id=55, now=now)
    assert event and event["dimensions"] == {"playfulness": .8, "energy": .3}
    assert "text" not in event
    assert capture_voice_feedback(db, user_id=user.id, text="بازیگوش‌تر باش", source_message_id=60,
                                  reply_to_message_id=55, now=now) is None
    assert capture_voice_feedback(db, user_id=user.id, text="امروز هوا خوبه", source_message_id=61,
                                  reply_to_message_id=None, now=now) is None
    profile = load_voice_feedback_profile(db, user_id=user.id)
    assert 0 < profile["playfulness"] < .75
    settings = SimpleNamespace(tts_male_default_voice="default", tts_male_playful_voice="playful",
        tts_male_calm_voice="calm", tts_female_default_voice="female", tts_female_playful_voice="female-playful")
    monkeypatch.setattr("app.llm.tts_client.get_settings", lambda: settings)
    assert select_tts_voice(user, {"gender": "male"}, "", "", {}) == "default"
    repeated = {"playfulness": .5}
    assert select_tts_voice(user, {"gender": "male"}, "", "", repeated) == "playful"
    assert select_tts_voice(user, {"gender": "female"}, "", "", repeated) == "female-playful"
