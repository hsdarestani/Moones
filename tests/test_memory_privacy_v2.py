"""Privacy, consent gates, and truthful embedding metering; offline only."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.models.memory import MemoryItem
from app.models.memory_embedding import MemoryEmbedding
from app.models.memory_privacy import MemoryPrivacyChoice, MemoryEmbeddingUsage
from app.models.temporal_memory import MemoryEvent, MemoryFact
from app.memory.privacy_controls import (
    has_semantic_consent, set_semantic_consent, clear_derived_memory,
    handle_memory_command, preference_summary,
)
from app.memory.temporal_store import record_explicit_fact
from app.memory.semantic_retrieval import (
    indexed_semantic_query, content_hash, _record_embedding_usage,
)
from scripts.index_memory_embeddings import index_user


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def fk_on(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    Base.metadata.create_all(engine, tables=[
        User.__table__, Message.__table__, MemoryItem.__table__,
        MemoryEvent.__table__, MemoryFact.__table__, MemoryEmbedding.__table__,
        MemoryPrivacyChoice.__table__, MemoryEmbeddingUsage.__table__,
    ])
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


def user(db, telegram_id):
    return db.execute(insert(User.__table__).values(telegram_id=telegram_id)).inserted_primary_key[0]


def source(db, uid, text):
    return db.execute(insert(Message.__table__).values(
        user_id=uid, role="user", input_type="text", content=text,
    )).inserted_primary_key[0]


def test_off_by_default_and_isolation(db):
    alice, bob = user(db, 500), user(db, 501)
    assert not has_semantic_consent(db, alice)
    assert not has_semantic_consent(db, bob)
    set_semantic_consent(db, alice, True)
    assert has_semantic_consent(db, alice)
    assert not has_semantic_consent(db, bob)
    assert "فعال" in handle_memory_command(db, alice, "/memory")
    assert "غیرفعال" in handle_memory_command(db, bob, "/memory")


def test_revocation_purges_own_vectors_not_raw_chat(db):
    alice, bob = user(db, 502), user(db, 503)
    ma, mb = source(db, alice, "یادت باشه اسم سگم پاستیله"), source(db, bob, "یادت باشه اسم سگم پنبه")
    for uid, mid, phrase in [(alice, ma, "یادت باشه اسم سگم پاستیله"),
                              (bob, mb, "یادت باشه اسم سگم پنبه")]:
        db.add(MemoryEmbedding(
            user_id=uid, source_message_id=mid,
            model_id="fake", content_sha256=content_hash(phrase), vector=[1.0, 0.0],
        ))
    db.flush()
    set_semantic_consent(db, alice, True)
    assert set_semantic_consent(db, alice, False) == 1
    assert not has_semantic_consent(db, alice)
    assert db.scalar(select(MemoryEmbedding).where(MemoryEmbedding.user_id == bob)) is not None
    assert db.get(Message, ma) is not None
    assert set_semantic_consent(db, alice, False) == 0


def test_clear_only_derived_data_and_revokes(db):
    alice = user(db, 504)
    txt = "یادت باشه اسم سگم پاستیله"
    mid = source(db, alice, txt)
    set_semantic_consent(db, alice, True)
    record_explicit_fact(db, user_id=alice, source_message_id=mid, text=txt)
    db.add(MemoryEmbedding(
        user_id=alice, source_message_id=mid, model_id="fake",
        content_sha256=content_hash(txt), vector=[1.0, 0.0],
    ))
    db.add(MemoryItem(user_id=alice, type="explicit_user", content=txt, importance_score=.8))
    db.add(MemoryEmbeddingUsage(user_id=alice, request_kind="query", model_id="fake", input_tokens=18))
    db.flush()
    result = clear_derived_memory(db, alice)
    assert result["vectors"] == 1 and result["facts"] == 1 and result["legacy"] == 1
    assert not has_semantic_consent(db, alice)
    assert db.get(Message, mid) is not None
    assert db.scalars(select(MemoryFact)).all() == []
    assert len(db.scalars(select(MemoryEmbeddingUsage)).all()) == 1  # financial audit without chat contents
    assert clear_derived_memory(db, alice)["facts"] == 0
    assert "تاریخچه" in handle_memory_command(db, alice, "/memory_clear")


def test_embedding_requires_database_consent_before_paid_call(db, monkeypatch):
    uid = user(db, 505)
    text = "یادت باشه تولدم آخر ماهه"
    mid = source(db, uid, text)
    db.add(MemoryEmbedding(user_id=uid, source_message_id=mid, model_id="text-embedding-bge-m3",
                           content_sha256=content_hash(text), vector=[1.0, 0.0]))
    db.flush()
    monkeypatch.setenv("MONES_SEMANTIC_MEMORY_ENABLED", "true")
    import app.memory.semantic_retrieval as service
    calls = []
    async def fake_embed(q, **kwargs):
        calls.append(q)
        return [1.0, 0.0]
    monkeypatch.setattr(service, "embed_query", fake_embed)
    assert asyncio.run(indexed_semantic_query(db, uid, "یادته تولدم؟")) is None
    assert calls == []
    set_semantic_consent(db, uid, True)
    assert asyncio.run(indexed_semantic_query(db, uid, "یادته تولدم؟")) == [1.0, 0.0]
    assert len(calls) == 1
    set_semantic_consent(db, uid, False)
    assert asyncio.run(indexed_semantic_query(db, uid, "یادته تولدم؟")) is None
    assert len(calls) == 1


def test_usage_records_only_provider_reported_values(db):
    uid = user(db, 506)
    _record_embedding_usage(db, uid, "query", "fake", {
        "usage": {"prompt_tokens": 32}
    })
    _record_embedding_usage(db, uid, "index", "fake", {
        "usage": {"total_tokens": 54, "cost_usd": "0.0007"}
    })
    records = db.scalars(select(MemoryEmbeddingUsage).order_by(MemoryEmbeddingUsage.id)).all()
    assert records[0].input_tokens == 32 and records[0].provider_cost_usd is None
    assert records[1].input_tokens == 54
    assert records[1].provider_cost_usd == Decimal("0.0007")


def test_index_requires_database_consent_even_if_allowlisted(db, monkeypatch):
    uid = user(db, 507)
    source(db, uid, "یادت باشه اسم سگم پاستیله")
    monkeypatch.setenv("MONES_SEMANTIC_OPT_IN_USER_IDS", str(uid))
    assert index_user(db, uid, 10, False)["pending"] == 1
    with pytest.raises(ValueError, match="persisted user consent"):
        index_user(db, uid, 10, True)


def test_revoke_while_embedding_response_in_flight_discards_vector_and_usage(db, monkeypatch):
    """No paid network: a fake response revokes consent before returning."""
    import app.memory.semantic_retrieval as service
    from unittest.mock import AsyncMock
    uid = user(db, 510)
    set_semantic_consent(db, uid, True)
    monkeypatch.setattr(service, "_credentials", lambda: ("fake", "https://invalid.local/embeddings"))
    monkeypatch.setattr(service, "current_embedding_rate", AsyncMock(return_value=Decimal("0.15")))

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"data": [{"index": 0, "embedding": [1.0, 0.0]}],
                    "usage": {"prompt_tokens": 30}}

    class FakeHTTP:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, *_, **__):
            set_semantic_consent(db, uid, False)
            return FakeResponse()

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeHTTP())
    assert asyncio.run(service.embed_query("یادت هست؟", db=db, user_id=uid)) is None
    assert not has_semantic_consent(db, uid)
    assert db.scalars(select(MemoryEmbeddingUsage)).all() == []


def test_reenable_during_embedding_does_not_authorize_old_response(db, monkeypatch):
    """Revoke then re-enable is a new consent decision, not stale permission."""
    import app.memory.semantic_retrieval as service
    from unittest.mock import AsyncMock
    uid = user(db, 511)
    set_semantic_consent(db, uid, True)
    monkeypatch.setattr(service, "_credentials", lambda: ("fake", "https://invalid.local/embeddings"))
    monkeypatch.setattr(service, "current_embedding_rate", AsyncMock(return_value=Decimal("0.15")))

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}

    class FakeHTTP:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, *_, **__):
            set_semantic_consent(db, uid, False)
            set_semantic_consent(db, uid, True)
            return FakeResponse()

    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: FakeHTTP())
    assert asyncio.run(service.embed_query("یادت هست؟", db=db, user_id=uid)) is None
    assert has_semantic_consent(db, uid)
    assert db.scalars(select(MemoryEmbeddingUsage)).all() == []


def test_inflight_index_cannot_resurrect_revoked_memory(db, monkeypatch):
    """Operator indexing must re-check consent after a (fake) provider call."""
    uid = user(db, 512)
    source(db, uid, "یادت باشه اسم سگم پاستیله")
    set_semantic_consent(db, uid, True)
    monkeypatch.setenv("MONES_SEMANTIC_OPT_IN_USER_IDS", str(uid))
    import scripts.index_memory_embeddings as index_module

    def fake_embed(texts, **kwargs):
        set_semantic_consent(db, uid, False)
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(index_module, "embed_batch", fake_embed)
    with pytest.raises(ValueError, match="consent changed"):
        index_user(db, uid, 10, True)
    assert db.scalars(select(MemoryEmbedding)).all() == []


def test_inflight_index_cannot_store_vector_for_edited_source(db, monkeypatch):
    """Embedding text must still match the current original source row."""
    uid = user(db, 513)
    mid = source(db, uid, "یادت باشه اسم سگم پاستیله")
    set_semantic_consent(db, uid, True)
    monkeypatch.setenv("MONES_SEMANTIC_OPT_IN_USER_IDS", str(uid))
    import scripts.index_memory_embeddings as index_module

    def fake_embed(texts, **kwargs):
        db.execute(Message.__table__.update().where(Message.id == mid).values(
            content="یادت باشه اسم سگم عوض شده"))
        return [[1.0, 0.0] for _ in texts]
    monkeypatch.setattr(index_module, "embed_batch", fake_embed)
    with pytest.raises(ValueError, match="Source messages changed"):
        index_user(db, uid, 10, True)
    assert db.scalars(select(MemoryEmbedding)).all() == []
