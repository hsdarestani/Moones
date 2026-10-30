"""Manual, explicit-consent semantic index creation. Does not run in chat.

Examples:
  python scripts/index_memory_embeddings.py --user-id 15 --limit 40
  MONES_SEMANTIC_OPT_IN_USER_IDS=15 python scripts/index_memory_embeddings.py \
      --user-id 15 --limit 40 --apply --consent-confirmed

Default is dry-run. Only explicit "remember this" text messages are eligible.
Never log user text or vectors. Provider embeddings cost real money.
"""
from __future__ import annotations

import argparse
import os
from sqlalchemy import or_, select

from app.db.session import SessionLocal
from app.models.message import Message
from app.models.memory_embedding import MemoryEmbedding
from app.models.memory_privacy import MemoryPrivacyChoice
from app.memory.privacy_controls import has_semantic_consent, semantic_consent_version
from app.memory.semantic_retrieval import content_hash, embed_batch, embedding_model

MARKERS = ("یادت باشه", "یادت بمونه", "به خاطر بسپار", "remember that", "please remember")


def opted_in(user_id: int) -> bool:
    allowed = {part.strip() for part in os.getenv("MONES_SEMANTIC_OPT_IN_USER_IDS", "").split(",")}
    return str(user_id) in allowed


def index_user(db, user_id: int, limit: int, apply: bool) -> dict[str, int | str]:
    model = embedding_model()
    # Only explicit requests from the source user; no scanning assistant output.
    clauses = [Message.content.ilike("%" + word + "%") for word in MARKERS]
    rows = db.scalars(select(Message).where(
        Message.user_id == user_id, Message.role == "user",
        Message.input_type == "text", or_(*clauses)
    ).order_by(Message.id.asc()).limit(limit)).all()
    seen = db.scalars(select(MemoryEmbedding).where(
        MemoryEmbedding.user_id == user_id, MemoryEmbedding.model_id == model
    )).all()
    by_message = {entry.source_message_id: entry for entry in seen}
    changed = [
        row for row in rows if row.content and
        (row.id not in by_message or by_message[row.id].content_sha256 != content_hash(row.content))
    ]
    if not apply:
        return {"eligible": len(rows), "pending": len(changed), "indexed": 0, "mode": "dry-run"}
    if not opted_in(user_id) or not has_semantic_consent(db, user_id):
        raise ValueError("Both persisted user consent and administrator allowlist are required")
    consent_version = semantic_consent_version(db, user_id)
    if consent_version is None:
        raise ValueError("Persisted consent is missing")
    indexed = 0
    for start in range(0, len(changed), 8):
        # Consent is checked before spending any money on this batch.
        if semantic_consent_version(db, user_id) != consent_version:
            raise ValueError("User consent changed before embedding request")
        batch = changed[start:start + 8]
        vectors = embed_batch([row.content for row in batch], db=db, user_id=user_id)
        if len(vectors) != len(batch):
            raise ValueError("Embedding count mismatch")
        # Never resurrect a revoked memory. PostgreSQL row locking serializes
        # this final check+insert with /memory_off or /memory_clear:
        # revoke first -> this check fails; index first -> revoke deletes it.
        consent = db.scalar(select(MemoryPrivacyChoice).where(
            MemoryPrivacyChoice.user_id == user_id
        ).with_for_update().execution_options(populate_existing=True))
        if (consent is None or not consent.semantic_opt_in
                or consent.changed_at != consent_version
                or not opted_in(user_id)):
            raise ValueError("User consent changed while embedding request was in flight")
        # The source may have been edited/deleted during the paid request.
        source_rows = db.execute(select(Message.id, Message.content).where(
            Message.user_id == user_id, Message.role == "user",
            Message.id.in_([row.id for row in batch])
        )).all()
        source_hashes = {mid: content_hash(content) for mid, content in source_rows}
        if any(source_hashes.get(row.id) != content_hash(row.content) for row in batch):
            raise ValueError("Source messages changed while embedding request was in flight")
        for row, vector in zip(batch, vectors):
            current = by_message.get(row.id)
            if current is None:
                current = MemoryEmbedding(
                    user_id=user_id, source_message_id=row.id, model_id=model,
                    content_sha256=content_hash(row.content), vector=vector
                )
                db.add(current)
            else:
                current.vector = vector
                current.content_sha256 = content_hash(row.content)
            indexed += 1
        db.commit()
    return {"eligible": len(rows), "pending": len(changed), "indexed": indexed, "mode": "applied"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Index opted-in Moones explicit memories")
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--consent-confirmed", action="store_true")
    args = parser.parse_args()
    if args.user_id < 1 or args.limit < 1 or args.limit > 500:
        parser.error("user-id positive; limit must be 1..500")
    if args.apply and (not args.consent_confirmed or not opted_in(args.user_id)):
        parser.error("--apply requires --consent-confirmed and admin allowlist; database user consent is also checked")
    with SessionLocal() as db:
        summary = index_user(db, args.user_id, args.limit, args.apply)
    print(summary)


if __name__ == "__main__":
    main()
