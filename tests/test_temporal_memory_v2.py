"""Temporal memory tests run on SQLite; no server or Venice key required."""
from __future__ import annotations

from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session
import pytest

from app.db.base import Base
from app.models.user import User
from app.models.message import Message
from app.models.temporal_memory import MemoryEvent, MemoryFact
from app.memory.temporal_store import _explicit_payload, record_explicit_fact, relevant_temporal_facts


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def fk_on(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    Base.metadata.create_all(engine, tables=[
        User.__table__, Message.__table__, MemoryEvent.__table__, MemoryFact.__table__,
    ])
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


def _user(db, telegram_id):
    result = db.execute(insert(User.__table__).values(telegram_id=telegram_id))
    return result.inserted_primary_key[0]


def _message(db, uid, text):
    result = db.execute(insert(Message.__table__).values(user_id=uid, role="user", content=text))
    return result.inserted_primary_key[0]


def test_only_explicit_unambiguous_stored():
    assert _explicit_payload("سلام خوبی؟") is None
    assert _explicit_payload("فراموش کن، یادت باشه اینو") is None
    assert _explicit_payload("یادت باشه اسم سگم پاستیله") == ("pet:dog", "name", "پاستیله")
    assert _explicit_payload("یادت باشه اسم سگم رو گذاشتم پنبه") == ("pet:dog", "name", "پنبه")
    assert _explicit_payload("یادت باشه سیاره مورد علاقم مریخه")[0] == "user"


def test_source_verified_user_isolation_and_idempotency(db):
    owner, other = _user(db, 112), _user(db, 113)
    source = _message(db, owner, "یادت باشه اسم سگم پاستیله")
    with pytest.raises(ValueError):
        record_explicit_fact(db, user_id=other, source_message_id=source, text="یادت باشه اسم سگم پاستیله")
    with pytest.raises(ValueError):
        record_explicit_fact(db, user_id=owner, source_message_id=source, text="یادت باشه اسم سگم پنبه")
    fact = record_explicit_fact(db, user_id=owner, source_message_id=source, text="یادت باشه اسم سگم پاستیله")
    same = record_explicit_fact(db, user_id=owner, source_message_id=source, text="یادت باشه اسم سگم پاستیله")
    assert fact.id == same.id
    assert len(db.scalars(select(MemoryEvent)).all()) == 1
    assert not db.scalars(select(MemoryFact).where(MemoryFact.user_id == other)).all()


def test_fact_supersession_preserves_history(db):
    owner = _user(db, 114)
    source1 = _message(db, owner, "یادت باشه اسم سگم پاستیله")
    old = record_explicit_fact(db, user_id=owner, source_message_id=source1, text="یادت باشه اسم سگم پاستیله")
    source2 = _message(db, owner, "یادت باشه اسم سگم رو گذاشتم پنبه")
    latest = record_explicit_fact(db, user_id=owner, source_message_id=source2, text="یادت باشه اسم سگم رو گذاشتم پنبه")
    assert old.valid_to is not None
    assert latest.valid_to is None
    assert latest.value == "پنبه"
    now = relevant_temporal_facts(db, user_id=owner, query="اسم سگ من چیه؟")
    assert any("پنبه" in v for v in now)
    assert not any("پاستیله" in v for v in now)
    past = relevant_temporal_facts(db, user_id=owner, query="اسم سگ من قبلاً چی بود؟")
    assert any("پاستیله" in v for v in past)
    assert any("پنبه" in v for v in past)


def test_no_other_users_fact_retrieval(db):
    alice, bob = _user(db, 115), _user(db, 116)
    msg = _message(db, alice, "یادت باشه اسم سگم پاستیله")
    record_explicit_fact(db, user_id=alice, source_message_id=msg, text="یادت باشه اسم سگم پاستیله")
    assert not relevant_temporal_facts(db, user_id=bob, query="اسم سگ من چیه؟")


def test_source_message_delete_cascades_to_fact(db):
    owner = _user(db, 117)
    msg = _message(db, owner, "یادت باشه اسم سگم پاستیله")
    record_explicit_fact(db, user_id=owner, source_message_id=msg, text="یادت باشه اسم سگم پاستیله")
    db.execute(Message.__table__.delete().where(Message.id == msg))
    db.expire_all()
    assert db.scalars(select(MemoryEvent)).all() == []
    assert db.scalars(select(MemoryFact)).all() == []
