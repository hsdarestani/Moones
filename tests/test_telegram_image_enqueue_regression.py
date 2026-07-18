import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.message import Message
from app.models.user import User
from app.models.image_generation import ImageGenerationJob
from app.api import telegram
from app.api.telegram import TelegramUpdate
from app.services.image_generation_service import ImageGenerationDenied
from app.services.usage_billing_service import InsufficientCoins


class _Flags:
    def __init__(self, execution_enabled):
        self.execution_enabled = execution_enabled


class _Settings:
    required_channel_enabled = False
    simple_chat_mode = False


async def _true(*args, **kwargs):
    return True


class _ForwardBatches:
    def key(self, *args):
        return "k"

    async def flush(self, *args, **kwargs):
        return None


class _Telegram:
    sent = []

    def __init__(self, bot_type):
        self.bot_type = bot_type

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return len(self.sent) + 1000

    async def send_text(self, chat_id, text, reply_markup=None, reply_to_message_id=None, allow_sending_without_reply=None):
        self.sent.append((chat_id, text, reply_markup))
        return len(self.sent) + 1000

    async def answer_callback_query(self, *args, **kwargs):
        return None


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, Message.__table__, ImageGenerationJob.__table__])
    return sessionmaker(bind=engine)()


def _update(text="یه عکس قدی بده", *, telegram_id=419, message_id=77, update_id=100):
    return TelegramUpdate.model_validate({
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": telegram_id, "first_name": "M"},
            "chat": {"id": telegram_id},
            "text": text,
        },
    })


def _user(db, telegram_id=419):
    user = User(telegram_id=telegram_id, display_name="M", onboarding_step="complete")
    db.add(user); db.commit()
    return user


def _patch_common(monkeypatch, *, semantic_enabled):
    _Telegram.sent = []
    monkeypatch.setattr(telegram.onboarding, "get_or_create_user", lambda db, telegram_id, display_name, locale=None: db.query(User).filter_by(telegram_id=telegram_id).one())
    monkeypatch.setattr(telegram, "TelegramService", _Telegram)
    monkeypatch.setattr(telegram, "forward_batches", _ForwardBatches())
    monkeypatch.setattr(telegram, "get_settings", lambda: _Settings())
    monkeypatch.setattr(telegram, "_check_required_channel", _true)
    monkeypatch.setattr(telegram, "capture_voice_feedback", lambda *args, **kwargs: None)
    monkeypatch.setattr(telegram, "_log_image_v2_route_shadow_if_enabled", lambda *args, **kwargs: False)
    monkeypatch.setattr(telegram, "resolve_semantic_router_flags", lambda db, user_id: _Flags(semantic_enabled))
    return _Telegram.sent


def test_semantic_router_execution_disabled_enqueues_without_unbound_pending_resolution(monkeypatch):
    db = _db(); _user(db)
    sent = _patch_common(monkeypatch, semantic_enabled=False)
    calls = []
    monkeypatch.setattr(telegram, "resolve_pending_image_clarification", lambda *a, **k: pytest.fail("pending resolution should not run"))
    monkeypatch.setattr(telegram, "enqueue_image_request", lambda *a, **k: calls.append(k))

    result = asyncio.run(telegram._handle(_update(), db, "chat"))

    assert result == {"ok": True}
    assert len(calls) == 1
    assert calls[0]["user_request"] == "یه عکس قدی بده"
    assert sent[-1][1] == "باشه، الان یه عکس برات می‌فرستم."


def test_semantic_router_enabled_no_pending_clarification_enqueues(monkeypatch):
    db = _db(); _user(db)
    _patch_common(monkeypatch, semantic_enabled=True)
    calls = []
    monkeypatch.setattr(telegram, "resolve_pending_image_clarification", lambda *a, **k: None)
    monkeypatch.setattr(telegram, "enqueue_image_request", lambda *a, **k: calls.append(k))

    asyncio.run(telegram._handle(_update("یه عکس بده", message_id=78), db, "chat"))

    assert len(calls) == 1
    assert calls[0]["user_request"] == "یه عکس بده"


class _Resolution:
    action = telegram.SemanticImageAction.GENERATE_NEW
    effective_request_text = "یه عکس تمام‌قد کنار پنجره بده"
    effective_source_telegram_message_id = 41


def test_semantic_router_enabled_resolved_clarification_uses_original_effective_request(monkeypatch):
    db = _db(); _user(db)
    _patch_common(monkeypatch, semantic_enabled=True)
    calls = []
    marked = []
    monkeypatch.setattr(telegram, "resolve_pending_image_clarification", lambda *a, **k: _Resolution())
    monkeypatch.setattr(telegram, "mark_image_clarification_resolved", lambda resolution, telegram_message_id: marked.append(telegram_message_id))
    monkeypatch.setattr(telegram, "enqueue_image_request", lambda *a, **k: calls.append(k))

    asyncio.run(telegram._handle(_update("عکس جدید", message_id=79), db, "chat"))

    assert calls[0]["user_request"] == _Resolution.effective_request_text
    assert marked == [79]


def test_user_outside_semantic_rollout_persists_image_job_exactly_once(monkeypatch):
    db = _db(); _user(db)
    _patch_common(monkeypatch, semantic_enabled=False)
    calls = []
    monkeypatch.setattr(telegram, "enqueue_image_request", lambda *a, **k: calls.append(k))

    asyncio.run(telegram._handle(_update(message_id=80), db, "chat"))

    assert len(calls) == 1


def test_duplicate_telegram_update_creates_at_most_one_image_job(monkeypatch):
    db = _db(); _user(db)
    _patch_common(monkeypatch, semantic_enabled=False)
    created_keys = set()
    def fake_enqueue(db, *, user, chat_id, source_telegram_message_id, user_request, route_decision):
        created_keys.add((user.telegram_id, chat_id, source_telegram_message_id, route_decision.route))
    monkeypatch.setattr(telegram, "enqueue_image_request", fake_enqueue)

    update = _update(message_id=81, update_id=201)
    asyncio.run(telegram._handle(update, db, "chat"))
    asyncio.run(telegram._handle(update, db, "chat"))

    assert len(created_keys) == 1


def test_addon_missing_message_remains_unchanged(monkeypatch):
    db = _db(); _user(db)
    sent = _patch_common(monkeypatch, semantic_enabled=False)
    monkeypatch.setattr(telegram, "enqueue_image_request", lambda *a, **k: (_ for _ in ()).throw(ImageGenerationDenied("addon_required")))
    monkeypatch.setattr(telegram, "management_bot_url", lambda start: f"https://manage/{start}")

    asyncio.run(telegram._handle(_update(message_id=82), db, "chat"))

    assert sent[-1][1] == "برای دریافت عکس از مونس، اول افزودنی «دریافت عکس از مونس» رو از ربات مدیریت فعال کن. هزینه هر عکس جداگانه با سکه کم می‌شه."


def test_insufficient_coins_message_remains_wallet_recharge(monkeypatch):
    db = _db(); _user(db)
    sent = _patch_common(monkeypatch, semantic_enabled=False)
    monkeypatch.setattr(telegram, "enqueue_image_request", lambda *a, **k: (_ for _ in ()).throw(InsufficientCoins(balance=1, required=2)))
    monkeypatch.setattr(telegram, "should_send_low_wallet_notice", lambda *a, **k: True)

    asyncio.run(telegram._handle(_update(message_id=83), db, "chat"))

    assert "سکه" in sent[-1][1]
    assert sent[-1][2] == telegram.recharge_keyboard()
