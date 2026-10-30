"""Offline Telegram webhook contract for privacy commands in both bots.

Do not contact Telegram, Venice, Redis, or real users.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.models.memory import MemoryItem
from app.models.memory_embedding import MemoryEmbedding
from app.models.memory_privacy import MemoryPrivacyChoice, MemoryEmbeddingUsage
from app.models.temporal_memory import MemoryFact, MemoryEvent
from app.memory.semantic_retrieval import content_hash
from app.memory.privacy_controls import has_semantic_consent
from app.api import telegram as tg


@pytest.fixture
def harness(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    Base.metadata.create_all(engine, tables=[
        User.__table__, Message.__table__, MemoryItem.__table__,
        MemoryEmbedding.__table__, MemoryPrivacyChoice.__table__,
        MemoryEmbeddingUsage.__table__, MemoryFact.__table__, MemoryEvent.__table__,
    ])
    outputs = []
    class FakeTelegram:
        def __init__(self, bot_type):
            self.bot_type = bot_type
        async def send_message(self, chat_id, text, reply_markup=None):
            outputs.append((self.bot_type, chat_id, text))
    monkeypatch.setattr(tg, "TelegramService", FakeTelegram)
    monkeypatch.setattr(tg, "ensure_signup_welcome_credit", lambda *a, **k: None)
    monkeypatch.setattr(tg, "resolve_reply_context", lambda *a, **k: None)
    monkeypatch.setattr(tg, "is_forwarded_message", lambda *a, **k: False)
    monkeypatch.setattr(tg.onboarding, "get_or_create_user",
                        lambda db, sender_id, *_: db.scalar(
                            select(User).where(User.telegram_id == sender_id)))
    async def no_forward_flush(*a, **k): pass
    monkeypatch.setattr(tg.forward_batches, "flush", no_forward_flush)
    with Session(engine) as db:
        db.add(User(telegram_id=88822, display_name="Synthetic Test", onboarding_step="complete"))
        db.commit()
        yield db, outputs
    engine.dispose()


def update(text, *, chat_id=88822):
    sender = SimpleNamespace(id=88822, first_name="Test", username="test_bot_user", language_code="fa")
    message = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id), from_user=sender,
        message_id=10, text=text, reply_to_message=None,
    )
    return SimpleNamespace(update_id=15, callback_query=None, message=message)


@pytest.mark.parametrize("bot", ["chat", "management"])
def test_private_memory_commands_skip_paywall_and_llm(harness, monkeypatch, bot):
    db, output = harness
    async def fail_llm(*a, **k):
        raise AssertionError("Memory settings must never call a paid model")
    monkeypatch.setattr(tg, "handle_simple_chat", fail_llm)
    uid = db.scalar(select(User.id).where(User.telegram_id == 88822))
    assert asyncio.run(tg._handle(update("/memory_on"), db, bot)) == {"ok": True}
    assert has_semantic_consent(db, uid)
    assert "ثبت شد" in output[-1][2]

    assert asyncio.run(tg._handle(update("/memory"), db, bot)) == {"ok": True}
    assert "فعال" in output[-1][2]
    assert asyncio.run(tg._handle(update("/memory_off"), db, bot)) == {"ok": True}
    assert not has_semantic_consent(db, uid)
    assert "لغو شد" in output[-1][2]


@pytest.mark.parametrize("bot", ["chat", "management"])
def test_group_memory_commands_never_change_private_consent(harness, bot):
    db, output = harness
    uid = db.scalar(select(User.id).where(User.telegram_id == 88822))
    asyncio.run(tg._handle(update("/memory_on", chat_id=-100238), db, bot))
    assert not has_semantic_consent(db, uid)
    assert "خصوصی" in output[-1][2]
    asyncio.run(tg._handle(update("/memory_clear@MoonesBot", chat_id=-100238), db, bot))
    assert not has_semantic_consent(db, uid)
    assert "خصوصی" in output[-1][2]


def test_clear_command_purges_derived_data_not_raw_chat_or_billing(harness):
    db, output = harness
    uid = db.scalar(select(User.id).where(User.telegram_id == 88822))
    message = Message(user_id=uid, role="user", input_type="text",
                      content="یادت باشه اسم سگم پاستیله")
    db.add(message)
    db.flush()
    db.add(MemoryItem(user_id=uid, type="explicit_user", content=message.content, importance_score=0.98))
    db.add(MemoryEmbedding(user_id=uid, source_message_id=message.id,
                           model_id="test", vector=[1.0, 0.0], content_sha256=content_hash(message.content)))
    db.add(MemoryEmbeddingUsage(user_id=uid, request_kind="query", model_id="test", input_tokens=5))
    db.commit()
    asyncio.run(tg._handle(update("/memory_on"), db, "chat"))
    asyncio.run(tg._handle(update("/memory_clear"), db, "chat"))
    assert db.scalars(select(MemoryEmbedding)).all() == []
    assert db.scalars(select(MemoryItem)).all() == []
    assert db.scalars(select(Message)).one().content == "یادت باشه اسم سگم پاستیله"
    assert len(db.scalars(select(MemoryEmbeddingUsage)).all()) == 1
    assert "پیام‌های اصلی" in output[-1][2]
