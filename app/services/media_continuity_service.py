from __future__ import annotations

from datetime import datetime, timedelta
import re
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.memory import MemoryItem

MEDIA_MEMORY_TYPE = "media_event"

IMAGE_DENIALS = ("عکس نمی‌فرستم", "عکس که نمی‌فرستم", "نه دیگه، عکس نمی‌فرستم", "نمی‌تونم عکس بفرستم")
VOICE_DENIALS = ("وویس نمی‌فرستم", "وویس که نمی‌فرستم", "نمی‌تونم وویس بفرستم", "ویس نمی‌فرستم")
IMAGE_CRITIQUE_RE = re.compile(r"اینجوری نیست|لم ندادی|خوب نشد|بهتر بده|دقیق درنیومد|پرتره شد|اون عکس|این عکس")


def record_media_delivery(
    db: Session,
    *,
    user_id: int,
    media_type: str,
    request_summary: str = "",
    generated_summary: str = "",
    telegram_message_id: int | None = None,
) -> MemoryItem:
    note = (
        f"recent_{media_type}_sent; status=sent; request={request_summary[:160]}; "
        f"generated={generated_summary[:220]}; telegram_message_id={telegram_message_id or ''}; "
        f"timestamp={datetime.utcnow().isoformat()}"
    )
    item = MemoryItem(user_id=user_id, type=MEDIA_MEMORY_TYPE, content=note, importance_score=0.95)
    db.add(item)
    db.flush()
    return item


def recent_media_events(db: Session, user_id: int, *, limit: int = 3, max_age_minutes: int = 180) -> list[MemoryItem]:
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    return list(db.scalars(
        select(MemoryItem)
        .where(MemoryItem.user_id == user_id, MemoryItem.type == MEDIA_MEMORY_TYPE, MemoryItem.created_at >= cutoff)
        .order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
        .limit(limit)
    ).all())


def format_recent_media_context(db: Session, user_id: int) -> str:
    events = recent_media_events(db, user_id)
    if not events:
        return ""
    lines = ["[Recent delivered media continuity]"]
    for e in events:
        if "recent_image_sent" in e.content:
            lines.append("- A generated image was sent recently. Treat it as actually sent; say «این عکس» / «اون عکسی که فرستادم». If the user critiques it, acknowledge the mismatch and offer/send a better photo. Never deny sending photos.")
        elif "recent_voice_sent" in e.content:
            lines.append("- A generated voice message was sent recently. Treat it as actually sent; never deny sending voice.")
    return "\n".join(lines)


def repair_media_denial(text: str, user_text: str, *, recent_image: bool = False, recent_voice: bool = False) -> str:
    out = text or ""
    if recent_image:
        for marker in IMAGE_DENIALS:
            out = out.replace(marker, "")
        if IMAGE_CRITIQUE_RE.search(user_text or ""):
            return "حق داری، این یکی دقیق درنیومد. بذار یه عکس بهتر با همون حال‌وهوا بفرستم."
    if recent_voice:
        for marker in VOICE_DENIALS:
            out = out.replace(marker, "")
        if not out.strip():
            return "آره، اون وویسی که فرستادم رو گفتم؛ اگه خواستی یه وویس دیگه هم می‌فرستم."
    return re.sub(r"\s+", " ", out).strip()
