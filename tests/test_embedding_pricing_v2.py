"""No network calls: validate monetary math against Venice-style model catalog."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models.user import User
from app.models.memory_privacy import MemoryPrivacyChoice, MemoryEmbeddingUsage
from app.memory.embedding_pricing import catalog_input_rate, cost_from_usage
from app.memory.semantic_retrieval import _record_embedding_usage, embed_query


CATALOG = {"data": [{
    "id": "text-embedding-bge-m3",
    "model_spec": {"pricing": {"input": {"usd": "0.150"}, "output": {"usd": "0.600"}}},
}]}


def test_exact_model_and_input_rate_not_output_rate():
    assert catalog_input_rate(CATALOG, "text-embedding-bge-m3") == Decimal("0.150")
    assert catalog_input_rate(CATALOG, "missing-model") is None
    assert catalog_input_rate({"data": []}, "text-embedding-bge-m3") is None
    assert catalog_input_rate({"data": [{"id": "a", "model_spec": {"pricing": {"input": {"usd": -10}}}}]}, "a") is None


def test_token_cost_math_uses_million_token_rate_and_missing_is_unknown():
    tokens, cost = cost_from_usage({"usage": {"prompt_tokens": 1200}}, Decimal("0.15"))
    assert tokens == 1200
    assert cost == Decimal("0.00018")
    assert cost_from_usage({"usage": {}}, Decimal("0.15")) == (None, None)
    assert cost_from_usage({"usage": {"prompt_tokens": "bad"}}, Decimal("0.15")) == (None, None)
    assert cost_from_usage({"usage": {"prompt_tokens": -1}}, Decimal("0.15")) == (None, None)


def test_accounting_storage_snapshots_the_rate_not_unverified_guess():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[User.__table__, MemoryPrivacyChoice.__table__, MemoryEmbeddingUsage.__table__])
    with Session(engine) as db:
        uid = db.execute(insert(User.__table__).values(telegram_id=88011)).inserted_primary_key[0]
        _record_embedding_usage(db, uid, "query", "text-embedding-bge-m3",
                                {"usage": {"prompt_tokens": 1200}}, Decimal("0.150"))
        usage = db.scalars(select(MemoryEmbeddingUsage)).one()
        assert usage.input_tokens == 1200
        assert usage.provider_cost_usd == Decimal("0.00018000")
        assert usage.price_usd_per_million == Decimal("0.150000")
        assert usage.pricing_basis == "venice_catalog"
    engine.dispose()


def test_unverified_rate_does_not_send_paid_query(monkeypatch):
    import app.memory.semantic_retrieval as service
    monkeypatch.setattr(service, "_credentials", lambda: ("test-key", "https://example.invalid/embeddings"))
    monkeypatch.setattr(service, "current_embedding_rate", AsyncMock(return_value=None))
    class BlockHTTP:
        def __init__(self, **kwargs):
            raise AssertionError("HTTP embedding call must not be attempted")
    monkeypatch.setattr(service.httpx, "AsyncClient", BlockHTTP)
    assert asyncio.run(embed_query("یادم هست؟")) is None
