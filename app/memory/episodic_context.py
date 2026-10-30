"""Budgeted, source-grounded, per-user episodic memory retrieval.

No extra LLM calls or DB migrations. messages remains the append-only source
of truth; memory_items holds durable explicit memories. All retrieval is scoped
to user_id. The system never treats its own generated replies as user facts.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from app.memory.temporal_store import relevant_temporal_facts
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem
from app.models.message import Message

logger = logging.getLogger(__name__)

STOP = frozenset({
    "من", "تو", "از", "به", "که", "این", "اون", "اونجا", "برای", "با", "چی", "چیه",
    "بود", "بودم", "هست", "هستم", "یه", "یک", "رو", "را", "هم", "کردم", "گفتم",
    "یادته", "یادت", "قبلا", "قبلاً", "وقتی", "درباره", "راجع", "چیزی", "چرا",
    "the", "and", "for", "that", "what", "you", "your", "remember", "about",
})
RECALL_TERMS = ("یادت", "یادته", "قبلاً", "قبلا", "اولین بار", "اولین پیام", "چند وقت پیش",
                "قبلی", "گذشته", "remember", "last time", "previously", "ago")
FIRST_TERMS = ("اولین پیام", "اولین بار", "first message", "first time")
EXPLICIT_MARKERS = ("یادت باشه", "یادت بمونه", "به خاطر بسپار", "به خاطر داشته باش",
                    "remember that", "please remember")
NEGATIVE_MARKERS = ("فراموش کن", "یادت نره که فراموش", "forget that", "don't remember")

@dataclass(frozen=True)
class MemoryContext:
    recent_block: str
    memory_notes: tuple[str, ...]
    evidence_count: int
    selected_message_ids: tuple[int, ...]
    selected_chars: int

def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return (text.lower().replace("ي", "ی").replace("ك", "ک")
            .replace("\u200c", " ").replace("\u0640", ""))

def _terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[\w\u0600-\u06ff]{3,}", _normalize(text))
            if word not in STOP and not word.isdigit()}

def _clip(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

def _score(text: str, terms: set[str], *, importance: float = 0.5) -> float:
    overlap = len(_terms(text) & terms)
    return overlap * 5.0 + max(0.0, min(1.0, importance))

def _render_recent(recent_messages: Iterable[Message], max_chars: int) -> str:
    from app.engine.simple_chat import sanitize_memory_content, _is_clean_memory_message
    eligible = [row for row in recent_messages if _is_clean_memory_message(row)]
    # Keep chronological coherence, preserving the most recent dialogue first.
    kept: list[str] = []
    used = 0
    for row in reversed(eligible):
        role = "user" if row.role == "user" else "partner"
        source = (row.metadata_json or {}).get("source") if isinstance(row.metadata_json, dict) else None
        if source == "proactive" and row.role == "assistant":
            role = "partner (earlier outreach)"
        line = f"{role}: {_clip(sanitize_memory_content(row.role, row.content), 205)}"
        if used + len(line) > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
        if len(kept) == 6:
            break
    return "\n".join(reversed(kept)) or "(no prior dialogue)"

def _recalled_messages(db: Session, user_id: int, query: str,
                       recent_ids: set[int], limit: int = 3) -> list[Message]:
    q = _normalize(query)
    if not any(phrase in q for phrase in RECALL_TERMS) and len(_terms(query)) < 2:
        return []
    if any(phrase in q for phrase in FIRST_TERMS):
        rows = db.scalars(select(Message).where(
            Message.user_id == user_id, Message.role == "user"
        ).order_by(Message.id.asc()).limit(4)).all()
        return [m for m in rows if m.id not in recent_ids][:limit]
    keywords = sorted(_terms(query), key=len, reverse=True)[:5]
    if not keywords:
        return []
    # PostgreSQL ILIKE works for Persian and Latin. The bounded result set avoids
    # passing the entire archive to Python or the generation model.
    clauses = [Message.content.ilike("%" + term.replace("%", r"\%").replace("_", r"\_") + "%", escape="\\")
               for term in keywords]
    rows = db.scalars(select(Message).where(
        Message.user_id == user_id,
        Message.role == "user",
        or_(*clauses),
    ).order_by(Message.id.desc()).limit(80)).all()
    rows = [m for m in rows if m.id not in recent_ids and (m.content or "").strip()]
    rows.sort(key=lambda m: (_score(m.content, set(keywords)), m.id), reverse=True)
    return rows[:limit]

def build_memory_context(db: Session, user_id: int, query: str,
                         recent_messages: list[Message], *,
                         max_recent_chars: int = 950,
                         max_memory_chars: int = 850,
                         temporal_enabled: bool = False) -> MemoryContext:
    """Compose small relevant context with source timestamps; never discard raw messages."""
    terms = _terms(query)
    recent_ids = {m.id for m in recent_messages if m.id is not None}
    recent_block = _render_recent(recent_messages, max_recent_chars)

    # Existing legacy durable facts remain accessible; no destructive migration.
    memories = db.scalars(select(MemoryItem).where(
        MemoryItem.user_id == user_id
    ).order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc()).limit(250)).all()
    ranked = sorted(memories, key=lambda m: (
        _score(m.content, terms, importance=float(m.importance_score or 0.0)),
        m.created_at or datetime.min, m.id or 0,
    ), reverse=True)
    relevant_count = sum(bool(_terms(item.content or "") & terms) for item in memories)
    is_recall = any(term in _normalize(query) for term in RECALL_TERMS)
    # For an explicit past-event question, unrelated high-importance memories
    # must not masquerade as evidence for the event being requested.
    if is_recall and terms and not relevant_count:
        ranked = []

    lines: list[str] = []
    seen: set[str] = set()
    remaining = max_memory_chars
    temporal_notes: list[str] = []
    if temporal_enabled:
        try:
            temporal_notes = relevant_temporal_facts(db, user_id=user_id, query=query)
        except Exception:
            logger.warning("TEMPORAL_MEMORY_READ_FAILED user_id=%s", user_id, exc_info=True)
            temporal_notes = []
        for note in temporal_notes:
            if len(note) + 1 <= remaining:
                lines.append(note)
                remaining -= len(note) + 1
    for item in ranked:
        raw = (item.content or "").strip()
        key = _normalize(raw)
        if not raw or key in seen:
            continue
        line = f"[saved {item.created_at:%Y-%m-%d}] {_clip(raw, 195)}"
        if len(line) + 1 > remaining:
            continue
        seen.add(key)
        lines.append(line)
        remaining -= len(line) + 1
        if len(lines) >= 4:
            break

    evidence = []
    # Retrieve original user speech, with provenance, when a question benefits
    # from it. Do not confuse an assistant's fictional dialogue with user facts.
    try:
        evidence = _recalled_messages(db, user_id, query, recent_ids)
    except Exception:
        logger.warning("EPISODIC_RETRIEVAL_FAILED user_id=%s", user_id, exc_info=True)
    selected_ids: list[int] = []
    for row in evidence:
        line = f"[user message #{row.id} {row.created_at:%Y-%m-%d}] {_clip(row.content, 235)}"
        if len(line) + 1 > remaining:
            continue
        lines.append(line)
        remaining -= len(line) + 1
        selected_ids.append(row.id)

    if is_recall and not selected_ids and relevant_count == 0 and not temporal_notes:
        warning = "No verified past memory was retrieved; do not guess or invent past events."
        if len(warning) + 1 <= remaining:
            lines.append(warning)
    return MemoryContext(
        recent_block=recent_block,
        memory_notes=tuple(lines),
        evidence_count=len(selected_ids),
        selected_message_ids=tuple(selected_ids),
        selected_chars=len(recent_block) + sum(map(len, lines)),
    )

def remember_explicit_statement(db: Session, user_id: int, user_text: str) -> bool:
    """Only save user-requested memories. Raw messages are persisted separately.

    Never invent emotional labels or promote model-generated claims to facts.
    Retention/deletion and privacy policy remain controlled by the existing app.
    """
    normalized = _normalize(user_text)
    if not any(marker in normalized for marker in EXPLICIT_MARKERS):
        return False
    if any(marker in normalized for marker in NEGATIVE_MARKERS):
        return False
    content = _clip(user_text.strip(), 350)
    if len(content) < 10:
        return False
    existing = db.scalar(select(MemoryItem.id).where(
        MemoryItem.user_id == user_id, MemoryItem.content == content
    ).limit(1))
    if existing is not None:
        return False
    db.add(MemoryItem(user_id=user_id, type="explicit_user", content=content, importance_score=0.95))
    return True
