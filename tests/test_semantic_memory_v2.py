"""Source integrity and rank tests for semantic index; never call a paid API."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.models.memory_embedding import MemoryEmbedding
from app.models.memory_privacy import MemoryPrivacyChoice
from app.memory.semantic_retrieval import (
    normalized_vector, similarity, content_hash, _parse_embeddings, semantic_candidates,
    indexed_semantic_query,
)

@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def fk_on(conn, _):
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    Base.metadata.create_all(engine, tables=[User.__table__, Message.__table__, MemoryEmbedding.__table__, MemoryPrivacyChoice.__table__])
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()

def add_user(db, telegram_id):
    return db.execute(insert(User.__table__).values(telegram_id=telegram_id)).inserted_primary_key[0]

def add_source(db, uid, text, vector, model="test-embedding"):
    mid = db.execute(insert(Message.__table__).values(
        user_id=uid, role="user", content=text, input_type="text"
    )).inserted_primary_key[0]
    db.add(MemoryEmbedding(
        user_id=uid, source_message_id=mid, model_id=model,
        content_sha256=content_hash(text), vector=vector
    ))
    db.flush()
    return mid

def test_vector_validation_and_order():
    assert normalized_vector([3, 4]) == pytest.approx([0.6, 0.8])
    assert similarity([1., 0.], [0., 1.]) == 0
    with pytest.raises(ValueError):
        normalized_vector([float("nan"), 1])
    with pytest.raises(ValueError):
        normalized_vector([0, 0])
    with pytest.raises(ValueError):
        _parse_embeddings({"data": [{"index": 1, "embedding": [1, 0]}]}, 1)

def test_semantic_recall_and_no_cross_user_leak(db):
    a = add_user(db, 2001)
    b = add_user(db, 2002)
    good = add_source(db, a, "می‌خواستم تولدم رو جشن بگیرم", [0.96, 0.28])
    add_source(db, a, "امروز هوا خوبه", [0., 1.])
    add_source(db, b, "تولد یک شخص دیگر", [1., 0.])
    rows = semantic_candidates(db, a, [1., 0.], model_id="test-embedding")
    assert [row.id for row in rows] == [good]
    assert semantic_candidates(db, b, [0., 1.], model_id="test-embedding") == []
    assert semantic_candidates(db, a, [1., 0.], model_id="wrong-model") == []

def test_stale_content_and_deleted_source_are_not_recalled(db):
    uid = add_user(db, 2003)
    mid = add_source(db, uid, "دلم گرفته", [1., 0.])
    db.execute(Message.__table__.update().where(Message.id == mid).values(content="تغییر یافت"))
    assert semantic_candidates(db, uid, [1., 0.], model_id="test-embedding") == []
    db.execute(Message.__table__.delete().where(Message.id == mid))
    db.expire_all()
    assert db.scalars(select(MemoryEmbedding)).all() == []

def test_indexed_query_skips_paid_api_when_unindexed(db, monkeypatch):
    uid = add_user(db, 2004)
    monkeypatch.setenv("MONES_SEMANTIC_MEMORY_ENABLED", "true")
    # Neither an index nor a source is available, so no external HTTP call occurs.
    import asyncio
    assert asyncio.run(indexed_semantic_query(db, uid, "یادت هست؟")) is None
