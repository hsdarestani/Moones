"""User-authorized temporal facts, sourced to the user's own message.

The message archive remains authoritative. This store is an index only;
never promote model output or inferred emotion into a user fact.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.temporal_memory import MemoryEvent, MemoryFact
from app.models.message import Message

REMEMBER = ("یادت باشه", "یادت بمونه", "به خاطر بسپار", "remember that", "please remember")
FORGET = ("فراموش کن", "forget that", "don't remember")


def _normal(text: str) -> str:
    return " ".join((text or "").lower().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").split())


def _explicit_payload(text: str) -> tuple[str, str, str] | None:
    t = _normal(text)
    if not any(t.startswith(trigger) for trigger in REMEMBER) or any(x in t for x in FORGET):
        return None
    tail = t
    for trigger in REMEMBER:
        if t.startswith(trigger):
            tail = t[len(trigger):].strip(" :،؛.!")
            break
    if len(tail) < 3:
        return None
    # Strict slots only, to avoid turning ambiguous language into a fake fact.
    for label, marker in (("pet:dog", "سگ"), ("pet:cat", "گربه")):
        m = re.fullmatch(r"اسم (?:%sم|%s من) (?:الان |رو |را )?(?:گذاشتم |شده |هست |بود )?(.{2,60})" % (marker, marker), tail)
        if m:
            return label, "name", m.group(1).strip(" .،")
    digest = hashlib.sha256(tail.encode("utf-8")).hexdigest()[:20]
    return "user", f"explicit:{digest}", tail[:350]


def record_explicit_fact(db: Session, *, user_id: int, source_message_id: int, text: str) -> MemoryFact | None:
    """Idempotent sourced update. Caller owns transaction and SQL migration 0042."""
    parsed = _explicit_payload(text)
    if parsed is None:
        return None
    source = db.scalar(select(Message).where(
        Message.id == source_message_id,
        Message.user_id == user_id,
        Message.role == "user",
    ))
    if source is None or _normal(source.content) != _normal(text):
        raise ValueError("Memory source must be an exact user-owned message")
    prior_event = db.scalar(select(MemoryEvent).where(
        MemoryEvent.user_id == user_id,
        MemoryEvent.source_message_id == source_message_id,
        MemoryEvent.kind == "explicit_assertion",
    ))
    if prior_event is not None:
        return db.scalar(select(MemoryFact).where(MemoryFact.source_event_id == prior_event.id))

    subject, predicate, value = parsed
    current = db.scalar(select(MemoryFact).where(
        MemoryFact.user_id == user_id,
        MemoryFact.subject_key == subject,
        MemoryFact.predicate == predicate,
        MemoryFact.valid_to.is_(None),
    ).with_for_update())
    at = datetime.utcnow()
    if current is not None:
        current.valid_to = at
        db.flush()  # Release active-slot partial unique index before insert.
    event = MemoryEvent(
        user_id=user_id, source_message_id=source_message_id,
        kind="explicit_assertion", occurred_at=at, created_at=at,
        payload={"subject": subject, "predicate": predicate},
    )
    db.add(event)
    db.flush()
    fact = MemoryFact(
        user_id=user_id, source_event_id=event.id, subject_key=subject,
        predicate=predicate, value=value, confidence=1.0,
        valid_from=at, valid_to=None,
    )
    db.add(fact)
    db.flush()
    return fact


def relevant_temporal_facts(db: Session, *, user_id: int, query: str, limit: int = 3) -> list[str]:
    """Render dated, sourced current facts; retain history for past-time questions."""
    terms = {w for w in re.findall(r"[\w\u0600-\u06ff]{3,}", _normal(query)) if w not in
             {"یادته", "قبلا", "قبلاً", "چیه", "بود", "هست", "برای", "وقتی", "چی", "من", "رو", "که"}}
    if not terms:
        return []
    asking_past = any(w in _normal(query) for w in ("قبلا", "قبلاً", "قبلی", "قدیم", "before", "used to"))
    rows = db.scalars(select(MemoryFact).where(
        MemoryFact.user_id == user_id,
        *([] if asking_past else [MemoryFact.valid_to.is_(None)]),
    ).order_by(MemoryFact.valid_from.desc()).limit(200)).all()
    labels = {("pet:dog", "name"): "اسم سگ من", ("pet:cat", "name"): "اسم گربه من"}
    selected = []
    for fact in rows:
        label = labels.get((fact.subject_key, fact.predicate), fact.value)
        tokens = set(re.findall(r"[\w\u0600-\u06ff]{3,}", _normal(label + " " + fact.value)))
        if not tokens.intersection(terms):
            continue
        status = "current" if fact.valid_to is None else f"historical until {fact.valid_to:%Y-%m-%d}"
        selected.append(f"[{status} {fact.valid_from:%Y-%m-%d} source-event #{fact.source_event_id}] {label}: {fact.value}")
        if len(selected) >= limit:
            break
    return selected
