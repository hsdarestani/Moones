"""Small source-backed plans and outcomes; no LLM extraction or notifications.

Uses the existing temporal event ledger, never assistant speech. The deliberately
limited parser only accepts direct first-person dated plans and explicit outcomes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.temporal_memory import MemoryEvent

TOPICS = {
    "interview": ("مصاحبه",),
    "exam": ("امتحان", "آزمون"),
    "trip": ("سفر", "پرواز"),
    "presentation": ("ارائه", "پرزنت"),
}
KINDS = ("thread_opened", "thread_resolved", "thread_cancelled")


def _normal(text: str) -> str:
    return " ".join(text.replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک").split())


def _ready(db: Session) -> bool:
    return "memory_events" in inspect(db.connection()).get_table_names()


def record_thread(db: Session, *, user_id: int, source: Message, local_date: date) -> MemoryEvent | None:
    if source.user_id != user_id or source.role != "user" or not source.id or not _ready(db):
        return None
    # Verify persisted ownership/content, not caller-provided metadata or AI output.
    owned = db.scalar(select(Message).where(Message.id == source.id, Message.user_id == user_id, Message.role == "user"))
    if owned is None or owned.content != source.content:
        return None
    text = _normal(source.content or "")
    if any(w in text for w in ("اگر", "شاید", "مثلا", "مثلاً", "فرض کن", "فراموش کن", "یادت نمونه", "یادت نماند", "میگه", "می گه", "گفت که", "ترجمه", "نمونه", "پرامپت", "بنویس")):
        return None
    topic = next((key for key, words in TOPICS.items() if any(word in text for word in words)), None)
    if not topic:
        return None
    resolution = any(word in text for word in ("تموم شد", "تمام شد", "تموم کردم", "انجام دادم", "دادم", "قبول شدم", "رد شدم", "برگشتم"))
    cancelled = any(word in text for word in ("کنسل شد", "لغو شد", "نمی رم", "نمیره", "ندارم"))
    first_person = bool(re.search(r"(?:دارم|می ?رم|می ?خوام|می ?دهم|می ?دم|دادم|کردم|شدم|برگشتم)(?:\s|$)|(?:مصاحبه|امتحان|آزمون|سفر|پرواز|ارائه)(?:م| ام)(?:\s|$)", text))
    offset = 2 if "پس فردا" in text or "پسفردا" in text else 1 if "فردا" in text else 0 if "امروز" in text else None
    if (resolution or cancelled) and not first_person:
        return None
    if not resolution and not cancelled and (not first_person or offset is None):
        return None
    # Exclude third-person plans, quotes and questions rather than guessing intent.
    if "؟" in text or "?" in text or '"' in text or "«" in text or any(w in text for w in ("دوستم", "خواهرم", "برادرم", "همکارم")):
        return None
    existing = db.scalar(select(MemoryEvent).where(
        MemoryEvent.user_id == user_id, MemoryEvent.source_message_id == source.id,
        MemoryEvent.kind.in_(KINDS),
    ))
    if existing:
        return existing
    kind = "thread_cancelled" if cancelled else "thread_resolved" if resolution else "thread_opened"
    if kind != "thread_opened":
        previous = db.scalars(select(MemoryEvent).where(
            MemoryEvent.user_id == user_id, MemoryEvent.kind.in_(KINDS),
        ).order_by(MemoryEvent.id.desc()).limit(50)).all()
        latest = next((row for row in previous if (row.payload or {}).get("topic") == topic), None)
        if not latest or latest.kind != "thread_opened":
            return None
    row = MemoryEvent(user_id=user_id, source_message_id=source.id, kind=kind,
        payload={"topic": topic, "due_date": (local_date + timedelta(days=offset)).isoformat() if offset is not None else None,
                 "source_hash": hashlib.sha256((source.content or "").encode()).hexdigest()})
    db.add(row)
    db.flush()
    return row


def thread_context(db: Session, *, user_id: int, local_date: date, query: str = "", limit: int = 3) -> list[str]:
    if not _ready(db):
        return []
    rows = db.execute(select(MemoryEvent, Message).join(Message, Message.id == MemoryEvent.source_message_id).where(
        MemoryEvent.user_id == user_id, Message.user_id == user_id, Message.role == "user",
        MemoryEvent.kind.in_(KINDS),
    ).order_by(MemoryEvent.id.desc()).limit(60)).all()
    seen: set[str] = set()
    notes = []
    for event, source in rows:
        payload = event.payload or {}
        topic = payload.get("topic")
        if topic not in TOPICS or topic in seen:
            continue
        if hashlib.sha256((source.content or "").encode()).hexdigest() != payload.get("source_hash"):
            continue
        seen.add(topic)
        relevant = any(word in _normal(query) for word in TOPICS[topic])
        if event.kind == "thread_opened":
            try:
                due = date.fromisoformat(payload["due_date"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (local_date - timedelta(days=2) <= due <= local_date + timedelta(days=2)) and not relevant:
                continue
            status = f"planned for {due.isoformat()}; outcome UNKNOWN"
        elif relevant:
            status = "cancelled by user" if event.kind == "thread_cancelled" else "user reported an outcome"
        else:
            continue
        notes.append(f"[user source #{source.id}; {status}] {source.content}\nUse only if relevant; do not invent success, failure or a reminder delivery. Do not repeat a follow-up already asked in recent dialogue.")
        if len(notes) >= limit:
            break
    return notes
