"""Opt-in hybrid semantic retrieval using Venice-compatible embeddings.

Only source-anchored user messages already indexed for the same model can be
returned. Model embedding calls are optional; lexical retrieval is the fallback.
No pgvector extension required for this bounded initial pilot.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory_embedding import MemoryEmbedding
from app.models.message import Message

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


async def embed_query(text: str, *, timeout_seconds: float = 2.0) -> list[float] | None:
    """One small, fail-open query call ONLY when semantic mode is enabled."""
    if not (text or "").strip():
        return None
    key, url = _credentials()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, headers={"Authorization": "Bearer " + key},
                                         json={"model": embedding_model(), "input": text[:1800],
                                               "encoding_format": "float"})
        response.raise_for_status()
        return _parse_embeddings(response.json(), 1)[0]
    except Exception as exc:
        # No user content or credentials in logs.
        logger.warning("SEMANTIC_QUERY_FALLBACK error_type=%s", type(exc).__name__)
        return None


def embed_batch(texts: list[str], *, timeout_seconds: float = 18.0) -> list[list[float]]:
    """Operator-only indexing; this function is never called on the chat hot path."""
    if not texts or len(texts) > 16 or any(not s.strip() for s in texts):
        raise ValueError("Expected 1..16 nonempty texts")
    key, url = _credentials()
    if not key:
        raise ValueError("Venice API key required for manual indexing")
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, headers={"Authorization": "Bearer " + key},
                               json={"model": embedding_model(), "input": [s[:1800] for s in texts],
                                     "encoding_format": "float"})
    response.raise_for_status()
    return _parse_embeddings(response.json(), len(texts))


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
