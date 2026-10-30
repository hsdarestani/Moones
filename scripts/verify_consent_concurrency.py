"""Isolated PostgreSQL 16 concurrency preflight for memory revocation.

Only runs with explicit MONES_CONSENT_RACE_CI=1 against localhost/mones_test.
Creates synthetic user/messages in disposable CI DB after Alembic upgrade.
Never calls Venice/Telegram or touches production accounts.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User
from app.models.message import Message
from app.models.memory_embedding import MemoryEmbedding
from app.models.memory_privacy import MemoryEmbeddingUsage
from app.memory.privacy_controls import has_semantic_consent, set_semantic_consent
from app.memory.semantic_retrieval import content_hash
import app.memory.semantic_retrieval as retrieval
import scripts.index_memory_embeddings as indexer


def _engine():
    url = get_settings().database_url
    engine = create_engine(url)
    if (os.getenv("MONES_CONSENT_RACE_CI") != "1"
            or engine.url.get_backend_name() != "postgresql"
            or engine.url.host not in {"localhost", "127.0.0.1"}
            or engine.url.database != "mones_test"):
        engine.dispose()
        raise RuntimeError("Refusing to run outside disposable localhost/mones_test with explicit CI opt-in")
    return engine


def _make_test_account(engine, telegram_id, original):
    with Session(engine) as db:
        row = User(telegram_id=telegram_id, onboarding_step="complete")
        db.add(row)
        db.flush()
        user_id = row.id
        message = Message(user_id=user_id, role="user", input_type="text", content=original)
        db.add(message)
        db.flush()
        source_id = message.id
        set_semantic_consent(db, user_id, True)
        db.commit()
    return user_id, source_id


def verify_inflight_index(engine):
    original = "یادت باشه اسم سگم پاستیله"
    uid, mid = _make_test_account(engine, 995100001, original)
    previous = os.environ.get("MONES_SEMANTIC_OPT_IN_USER_IDS")
    original_embed = indexer.embed_batch
    os.environ["MONES_SEMANTIC_OPT_IN_USER_IDS"] = str(uid)
    hits = []
    def fake_embed(texts, **kwargs):
        assert texts == [original]
        with Session(engine) as other:
            removed = set_semantic_consent(other, uid, False)
            other.commit()
            hits.append(("revoked", removed))
        return [[1.0, 0.0]]
    indexer.embed_batch = fake_embed
    try:
        with Session(engine) as worker:
            try:
                indexer.index_user(worker, uid, limit=4, apply=True)
            except ValueError as exc:
                assert "consent changed" in str(exc), str(exc)
                worker.rollback()
            else:
                raise AssertionError("Indexing resurrected an embedding after remote revocation")
    finally:
        indexer.embed_batch = original_embed
        if previous is None:
            os.environ.pop("MONES_SEMANTIC_OPT_IN_USER_IDS", None)
        else:
            os.environ["MONES_SEMANTIC_OPT_IN_USER_IDS"] = previous
    assert len(hits) == 1
    with Session(engine) as db:
        assert not has_semantic_consent(db, uid)
        assert not db.scalars(select(MemoryEmbedding).where(MemoryEmbedding.user_id == uid)).all()
        assert db.get(Message, mid).content == original


def verify_source_edit_during_index(engine):
    original = "یادت باشه اولین سفرم تبریز بود"
    uid, _ = _make_test_account(engine, 995100002, original)
    previous = os.environ.get("MONES_SEMANTIC_OPT_IN_USER_IDS")
    original_embed = indexer.embed_batch
    os.environ["MONES_SEMANTIC_OPT_IN_USER_IDS"] = str(uid)

    def fake_embed(texts, **kwargs):
        with Session(engine) as updater:
            original_row = updater.scalar(select(Message).where(Message.user_id == uid))
            original_row.content = "یادت باشه اولین سفرم اصفهان بود"
            updater.commit()
        return [[1.0, 0.0]]

    indexer.embed_batch = fake_embed
    try:
        with Session(engine) as worker:
            try:
                indexer.index_user(worker, uid, limit=4, apply=True)
            except ValueError as exc:
                assert "Source messages changed" in str(exc), str(exc)
                worker.rollback()
            else:
                raise AssertionError("Modified source was still indexed")
    finally:
        indexer.embed_batch = original_embed
        if previous is None:
            os.environ.pop("MONES_SEMANTIC_OPT_IN_USER_IDS", None)
        else:
            os.environ["MONES_SEMANTIC_OPT_IN_USER_IDS"] = previous

    with Session(engine) as db:
        assert not db.scalars(select(MemoryEmbedding).where(MemoryEmbedding.user_id == uid)).all()
        assert has_semantic_consent(db, uid)


def verify_paid_query_discarded_after_revoke(engine):
    original = "یادت باشه اسم سگم کوکی بود"
    uid, mid = _make_test_account(engine, 995100003, original)
    with Session(engine) as db:
        db.add(MemoryEmbedding(
            user_id=uid, source_message_id=mid, model_id="fake-test",
            content_sha256=content_hash(original), vector=[1., 0.],
        ))
        db.commit()

    orig_credentials = retrieval._credentials
    orig_rate = retrieval.current_embedding_rate
    orig_client = retrieval.httpx.AsyncClient
    retrieval._credentials = lambda: ("test-key", "https://invalid.local/embeddings")
    retrieval.current_embedding_rate = AsyncMock(return_value=Decimal("0.15"))

    class Response:
        def raise_for_status(self): return None
        def json(self):
            return {"data": [{"index": 0, "embedding": [1., 0.]}],
                    "usage": {"prompt_tokens": 20}}

    class FakeHTTP:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def post(self, *_args, **_kwargs):
            with Session(engine) as other:
                set_semantic_consent(other, uid, False)
                other.commit()
            return Response()
    retrieval.httpx.AsyncClient = lambda **kwargs: FakeHTTP()
    try:
        with Session(engine) as worker:
            assert asyncio.run(retrieval.embed_query("یادته سگم؟", db=worker, user_id=uid)) is None
            worker.rollback()
    finally:
        retrieval._credentials = orig_credentials
        retrieval.current_embedding_rate = orig_rate
        retrieval.httpx.AsyncClient = orig_client

    with Session(engine) as db:
        assert not has_semantic_consent(db, uid)
        assert not db.scalars(select(MemoryEmbedding).where(MemoryEmbedding.user_id == uid)).all()
        assert not db.scalars(select(MemoryEmbeddingUsage).where(MemoryEmbeddingUsage.user_id == uid)).all()


def main():
    engine = _engine()
    try:
        verify_inflight_index(engine)
        verify_source_edit_during_index(engine)
        verify_paid_query_discarded_after_revoke(engine)
        print("Consent race checks passed: remote revoke; edited source; in-flight query")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
