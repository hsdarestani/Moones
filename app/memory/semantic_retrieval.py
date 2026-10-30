"""Opt-in hybrid semantic retrieval using Venice-compatible embeddings.

Only source-anchored user messages already indexed for the same model can be
returned. Model embedding calls are optional; lexical retrieval is the fallback.
No pgvector extension required for this bounded initial pilot.
"""
from __future__ import annotations

import hashlib
import logging
import math
from decimal import Decimal, InvalidOperation
import os
from typing import Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory_embedding import MemoryEmbedding
from app.models.message import Message
from app.models.memory_privacy import MemoryEmbeddingUsage
from app.memory.privacy_controls import has_semantic_consent
from app.memory.embedding_pricing import current_embedding_rate, current_embedding_rate_sync, cost_from_usage

logger = logging.getLogger(__name__)
DEFAULT_MODEL = "text-embedding-bge-m3"


def embedding_model() -> str:
    return os.getenv("MONES_SEMANTIC_EMBEDDING_MODEL", DEFAULT_MODEL).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def normalized_vector(values: Sequence[float]) -> list[float]:
    if not isinstance(values, (tuple, list)) or not 2 <= len(values) <= 4096:
        raise ValueError("Unexpected embedding dimensions")
    vec = [float(v) for v in values]
    if not all(math.isfinite(v) for v in vec):
        raise ValueError("Invalid embedding values")
    magnitude = math.sqrt(sum(v * v for v in vec))
    if magnitude <= 1e-12:
        raise ValueError("Zero embedding vector")
    return [v / magnitude for v in vec]


def similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        return -1.0
    return sum(x * y for x, y in zip(a, b))


def _parse_embeddings(data: dict, expected: int) -> list[list[float]]:
    entries = data.get("data")
    if not isinstance(entries, list) or len(entries) != expected:
        raise ValueError("Unexpected embedding count")
    ordered = sorted(entries, key=lambda entry: entry.get("index", -1))
    if [item.get("index") for item in ordered] != list(range(expected)):
        raise ValueError("Missing embedding index")
    vectors = [normalized_vector(item.get("embedding")) for item in ordered]
    if len({len(v) for v in vectors}) != 1:
        raise ValueError("Inconsistent embedding dimensionality")
    return vectors


def _credentials() -> tuple[str, str]:
    from app.core.config import get_settings
    cfg = get_settings()
    return (cfg.venice_api_key,
            cfg.venice_api_base_url.rstrip("/") + "/embeddings")


def _record_embedding_usage(db: Session, user_id: int, kind: str,
                            model_id: str, payload: dict,
                            rate_per_million: Decimal | None = None) -> None:
    """Provider token count x verified live catalog rate, with source metadata.

    Unknown provider token usage or unavailable pricing remain NULL, never zero.
    """
    if rate_per_million is not None:
        tokens, calculated = cost_from_usage(payload, rate_per_million)
        basis = "venice_catalog" if calculated is not None else "usage_missing"
    else:
        usage = payload.get("usage") or {}
        tokens = usage.get("prompt_tokens", usage.get("total_tokens"))
        try:
            tokens = int(tokens) if tokens is not None and not isinstance(tokens, bool) else None
            if tokens is not None and tokens < 0:
                tokens = None
        except (ValueError, TypeError, OverflowError):
            tokens = None
        calculated = None
        basis = "price_unavailable"
        # When Venice reports a direct USD charge, prefer that invoice-grade
        # value over an estimate; reject malformed, negative or infinite values.
        raw = usage.get("cost_usd") if isinstance(usage, dict) else None
        if raw is not None:
            try:
                vendor_cost = Decimal(str(raw))
                if vendor_cost.is_finite() and vendor_cost >= 0:
                    calculated = vendor_cost
                    basis = "provider_reported"
            except (ValueError, TypeError, InvalidOperation):
                pass
    db.add(MemoryEmbeddingUsage(
        user_id=user_id, request_kind=kind, model_id=model_id,
        input_tokens=tokens, provider_cost_usd=calculated,
        price_usd_per_million=rate_per_million, pricing_basis=basis,
    ))
    db.flush()


async def embed_query(text: str, *, db: Session | None = None, user_id: int | None = None, timeout_seconds: float = 2.0) -> list[float] | None:
    """One small, fail-open query call ONLY when semantic mode is enabled."""
    if not (text or "").strip():
        return None
    key, url = _credentials()
    if not key:
        return None
    try:
        rate = await current_embedding_rate(embedding_model())
        if rate is None:
            logger.warning("EMBEDDING_PRICE_UNVERIFIED_SKIP model=%s", embedding_model())
            return None
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers={"Authorization": "Bearer " + key},
                                         json={"model": embedding_model(), "input": text[:1800],
                                               "encoding_format": "float"})
        response.raise_for_status()
        payload = response.json()
        vector = _parse_embeddings(payload, 1)[0]
        if db is not None and user_id is not None:
            # The user may revoke consent while the network request is in flight.
            # Never reuse its vector after revocation.
            if not has_semantic_consent(db, user_id):
                return None
            _record_embedding_usage(db, user_id, "query", embedding_model(), payload, rate)
        return vector
    except Exception as exc:
        # No user content or credentials in logs.
        logger.warning("SEMANTIC_QUERY_FALLBACK error_type=%s", type(exc).__name__)
        return None


def embed_batch(texts: list[str], *, db: Session | None = None, user_id: int | None = None, timeout_seconds: float = 18.0) -> list[list[float]]:
    """Operator-only indexing; this function is never called on the chat hot path."""
    if not texts or len(texts) > 16 or any(not s.strip() for s in texts):
        raise ValueError("Expected 1..16 nonempty texts")
    key, url = _credentials()
    if not key:
        raise ValueError("Venice API key required for manual indexing")
    rate = current_embedding_rate_sync(embedding_model())
    if rate is None:
        raise ValueError("Venice embedding price unavailable; indexing stopped before paid request")
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, headers={"Authorization": "Bearer " + key},
                               json={"model": embedding_model(), "input": [s[:1800] for s in texts],
                                     "encoding_format": "float"})
    response.raise_for_status()
    payload = response.json()
    result = _parse_embeddings(payload, len(texts))
    if db is not None and user_id is not None:
        _record_embedding_usage(db, user_id, "index", embedding_model(), payload, rate)
    return result


def semantic_candidates(db: Session, user_id: int, query_vector: Sequence[float],
                        *, model_id: str, recent_ids: set[int] | None = None,
                        limit: int = 3, min_similarity: float = 0.38) -> list[Message]:
    """Bounded cosine rerank. Strict user isolation, source verification and hash checks.

    This uses a JSON vector index for a pilot, not an ANN search: it scans
    up to 600 indexed messages per user. Use pgvector/ANN before larger scale.
    """
    query_vec = normalized_vector(query_vector)
    rows = db.scalars(
        select(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id, MemoryEmbedding.model_id == model_id
        ).order_by(MemoryEmbedding.id.desc()).limit(600)
    ).all()
    seen_recent = recent_ids or set()
    ranked: list[tuple[float, int]] = []
    for row in rows:
        if row.source_message_id in seen_recent:
            continue
        try:
            score = similarity(query_vec, normalized_vector(row.vector))
        except (ValueError, TypeError, OverflowError):
            continue
        if score >= min_similarity:
            ranked.append((score, row.source_message_id))
    ranked.sort(reverse=True)
    best = ranked[: max(limit * 6, 12)]
    if not best:
        return []
    ids = [message_id for _, message_id in best]
    originals = db.scalars(select(Message).where(
        Message.user_id == user_id, Message.role == "user", Message.id.in_(ids)
    )).all()
    by_id = {message.id: message for message in originals}
    hashes = {row.source_message_id: row.content_sha256 for row in rows if row.source_message_id in ids}
    selected: list[Message] = []
    for _, mid in best:
        message = by_id.get(mid)
        if message is None or content_hash(message.content) != hashes.get(mid):
            continue
        if any(existing.id == mid for existing in selected):
            continue
        selected.append(message)
        if len(selected) >= limit:
            break
    return selected

RECALL_MARKERS = ("یادت", "قبلاً", "قبلا", "قبلش", "قدیم", "یادآوری",
                  "remember", "last time", "previous", "what did i")


async def indexed_semantic_query(db: Session, user_id: int, text: str) -> list[float] | None:
    """Do not bill embedding calls for users without an opted-in index."""
    if os.getenv("MONES_SEMANTIC_MEMORY_ENABLED", "").lower() not in {"1", "true", "yes", "on"}:
        return None
    if not has_semantic_consent(db, user_id):
        return None
    if not any(marker in (text or "").lower() for marker in RECALL_MARKERS):
        return None
    exists = db.scalar(select(MemoryEmbedding.id).where(
        MemoryEmbedding.user_id == user_id,
        MemoryEmbedding.model_id == embedding_model()
    ).limit(1))
    if exists is None:
        return None
    return await embed_query(text, db=db, user_id=user_id)
