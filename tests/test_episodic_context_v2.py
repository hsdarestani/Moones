from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.memory.episodic_context import (
    _normalize, _terms, build_memory_context, remember_explicit_statement,
)

def row(*, id, role, content, metadata=None):
    return SimpleNamespace(
        id=id, role=role, content=content,
        metadata_json=metadata or {}, created_at=datetime(2026, 9, 12),
    )

def memory(id, content, importance=.6):
    return SimpleNamespace(id=id, content=content, importance_score=importance,
                           created_at=datetime(2026, 1, 3))

class FakeDB:
    def __init__(self, memories=(), messages=(), existing=None):
        self.memories = list(memories)
        self.messages = list(messages)
        self.existing = existing
        self.queries = []
        self.added = []

    def scalars(self, statement):
        rendered = str(statement.compile(compile_kwargs={"literal_binds": True}))
        self.queries.append(rendered)
        if "FROM memory_items" in rendered:
            return SimpleNamespace(all=lambda: self.memories)
        if "FROM messages" in rendered:
            return SimpleNamespace(all=lambda: self.messages)
        raise AssertionError(rendered)

    def scalar(self, statement):
        self.queries.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
        return self.existing

    def add(self, obj):
        self.added.append(obj)

def test_persian_search_normalization():
    assert _normalize("كتاب مي\u200cخوانم") == "کتاب می خوانم"
    assert "تولد" in _terms("یادته تولد من چی شد؟")
    assert "یادت" not in _terms("یادت هست؟")

def test_budget_recent_and_source_grounded_recall():
    db = FakeDB(
        memories=[memory(3, "کاربر از سورپرایز تولد خوشش می آید", .8)],
        messages=[row(id=47, role="user", content="پار سال تولدم را کسی یادش نبود")],
    )
    recent = [
        row(id=i, role="assistant" if i % 2 else "user",
            content="امروز درباره یک فیلم جدید صحبت کردیم " * 5)
        for i in range(70, 82)
    ]
    result = build_memory_context(
        db, 99, "یادته درباره تولدم حرف زدیم؟", recent,
        max_recent_chars=500, max_memory_chars=360,
    )
    assert len(result.recent_block) <= 500
    assert sum(len(x) + 1 for x in result.memory_notes) <= 360
    assert result.selected_message_ids == (47,)
    assert "message #47" in "\n".join(result.memory_notes)
    assert all("user_id = 99" in query for query in db.queries)

def test_no_cross_user_recall_fetched_by_query():
    db = FakeDB(
        memories=[memory(1, "کاربر گفته اهل کوهنوردی است")],
        messages=[],
    )
    build_memory_context(db, 17, "یادت کوهنوردی؟", [])
    assert db.queries and all("user_id = 17" in q for q in db.queries)

def test_no_assistant_claims_as_archival_user_evidence():
    db = FakeDB(memories=[], messages=[])
    result = build_memory_context(db, 1, "یادته قبلاً چی گفتم؟", [])
    assert result.evidence_count == 0
    assert any("do not guess" in x for x in result.memory_notes)

def test_only_explicit_memory_and_idempotency():
    db = FakeDB()
    assert not remember_explicit_statement(db, 12, "سلام خوبی؟")
    assert not remember_explicit_statement(db, 12, "فراموش کن، یادت باشه حذفش کنی")
    assert remember_explicit_statement(db, 12, "یادت باشه اسم سگم پاستیله")
    assert db.added[0].type == "explicit_user"
    assert db.added[0].user_id == 12
    assert "اسم سگم" in db.added[0].content
    db.existing = 13
    assert not remember_explicit_statement(db, 12, "یادت باشه اسم سگم پاستیله")
    assert len(db.added) == 1
