"""Deterministic, privacy-safe synthetic benchmark (NOT production savings).

Run: python -m scripts.benchmark_memory_v2
Uses fake messages and fake 2-D embeddings, making zero model/API calls.
Reports *characters*, not token usage, and demonstrates retrieval plumbing only.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.memory import MemoryItem
from app.models.message import Message
from app.models.memory_embedding import MemoryEmbedding
from app.models.user import User
from app.memory.episodic_context import build_memory_context
from app.memory.semantic_retrieval import content_hash


def measure() -> dict:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        User.__table__, Message.__table__, MemoryItem.__table__, MemoryEmbedding.__table__,
    ])
    with Session(engine) as db:
        user_id = db.execute(insert(User.__table__).values(telegram_id=8799001)).inserted_primary_key[0]
        memory_text = "وقتی تولدم رو فراموش کردن احساس تنهایی عجیبی داشتم"
        mid = db.execute(insert(Message.__table__).values(
            user_id=user_id, role="user", content=memory_text, input_type="text",
        )).inserted_primary_key[0]
        for n in range(48):
            db.execute(insert(Message.__table__).values(
                user_id=user_id,
                role="assistant" if n % 2 else "user",
                content=f"گفتگوی آزمایشی {n}: موضوع روزمره و برنامه های عادی " * 4,
                input_type="text",
            ))
        db.add(MemoryEmbedding(
            user_id=user_id, source_message_id=mid,
            model_id="synthetic-only", content_sha256=content_hash(memory_text),
            vector=[1.0, 0.0],
        ))
        db.flush()
        messages = db.scalars(select(Message).where(Message.user_id == user_id).order_by(Message.id)).all()
        query = "یادته اون روز که خیلی ناراحت شدم چی بود؟"
        lexical = build_memory_context(db, user_id, query, messages[-12:])
        hybrid = build_memory_context(db, user_id, query, messages[-12:],
                                      semantic_vector=[1., 0.],
                                      semantic_model="synthetic-only")
        result = {
            "data": "synthetic-only; no real provider calls or user history",
            "metric": "characters, not tokens/currency",
            "full_history_chars": sum(len(m.content) for m in messages),
            "lexical_context_chars": lexical.selected_chars,
            "hybrid_context_chars": hybrid.selected_chars,
            "target_source_id": mid,
            "lexical_source_hit": mid in lexical.selected_message_ids,
            "hybrid_source_hit": mid in hybrid.selected_message_ids,
        }
        assert result["hybrid_source_hit"], "Expected semantic retrieval on synthetic vector"
        assert result["hybrid_context_chars"] < result["full_history_chars"]
    engine.dispose()
    return result


if __name__ == "__main__":
    print(json.dumps(measure(), ensure_ascii=False, indent=2))
