"""Account-owned memory preferences with explicit revocation and no silent index backfill.

These controls delete only derived memory. Original Telegram messages remain
in the conversation archive; never tell a user that all history was erased.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.memory_privacy import MemoryPrivacyChoice, MemoryEmbeddingUsage
from app.models.memory_embedding import MemoryEmbedding
from app.models.temporal_memory import MemoryEvent, MemoryFact
from app.models.memory import MemoryItem


def has_semantic_consent(db: Session, user_id: int) -> bool:
    return bool(db.scalar(select(MemoryPrivacyChoice.semantic_opt_in).where(
        MemoryPrivacyChoice.user_id == user_id
    )))


def preference_summary(db: Session, user_id: int) -> dict:
    """No stored text is ever exposed; only own-user status and counts."""
    return {
        "consented": has_semantic_consent(db, user_id),
        "vectors": int(db.scalar(select(func.count(MemoryEmbedding.id)).where(
            MemoryEmbedding.user_id == user_id
        )) or 0),
        "temporal_facts": int(db.scalar(select(func.count(MemoryFact.id)).where(
            MemoryFact.user_id == user_id
        )) or 0),
        "legacy_memories": int(db.scalar(select(func.count(MemoryItem.id)).where(
            MemoryItem.user_id == user_id
        )) or 0),
    }


def set_semantic_consent(db: Session, user_id: int, enabled: bool) -> int:
    """Owner-authenticated caller; repeated disable is idempotent.

    Revocation removes vectors synchronously within the transaction so there
    is no background provider search after consent is withdrawn.
    """
    row = db.get(MemoryPrivacyChoice, user_id)
    if row is None:
        row = MemoryPrivacyChoice(user_id=user_id, semantic_opt_in=enabled, changed_at=datetime.utcnow())
        db.add(row)
    else:
        row.semantic_opt_in = enabled
        row.changed_at = datetime.utcnow()
    deleted = 0
    if not enabled:
        deleted = db.execute(delete(MemoryEmbedding).where(
            MemoryEmbedding.user_id == user_id
        )).rowcount or 0
    db.flush()
    return int(deleted)


def clear_derived_memory(db: Session, user_id: int) -> dict:
    """Revoke and remove own derived memory; leave raw chat archive untouched."""
    deleted_vectors = set_semantic_consent(db, user_id, False)
    deleted_facts = db.execute(delete(MemoryFact).where(
        MemoryFact.user_id == user_id
    )).rowcount or 0
    deleted_events = db.execute(delete(MemoryEvent).where(
        MemoryEvent.user_id == user_id
    )).rowcount or 0
    deleted_legacy = db.execute(delete(MemoryItem).where(
        MemoryItem.user_id == user_id
    )).rowcount or 0
    # Keep financial audit rows: they contain only user ID, model, token count,
    # source of pricing, and cost, but no raw conversation or vectors.
    db.flush()
    return {
        "vectors": int(deleted_vectors),
        "facts": int(deleted_facts),
        "events": int(deleted_events),
        "legacy": int(deleted_legacy),
    }


def handle_memory_command(db: Session, user_id: int, command: str) -> str | None:
    command = command.split("@", 1)[0].lower().strip()
    if command == "/memory":
        summary = preference_summary(db, user_id)
        state = "فعال (با رضایت تو)" if summary["consented"] else "غیرفعال"
        return (
            "حافظه معنایی مونس 🧠\n"
            f"اجازه پردازش معنایی: {state}\n"
            f"خاطرات برداری: {summary['vectors']}\n"
            f"خاطرات زمانی: {summary['temporal_facts']}\n"
            f"خاطرات قدیمی ذخیره‌شده: {summary['legacy_memories']}\n\n"
            "/memory_on — اجازه ساخت حافظه معنایی برای آینده\n"
            "/memory_off — لغو اجازه و حذف بردارهای معنایی\n"
            "/memory_clear — حذف حافظه‌های مشتق‌شده\n\n"
            "توجه: تاریخچه خام چت و سوابق مالی با این دستورات پاک نمی‌شوند."
        )
    if command == "/memory_on":
        set_semantic_consent(db, user_id, True)
        return (
            "اجازه حافظه معنایی ثبت شد ✅\n"
            "این اجازه به‌تنهایی هیچ پیام قدیمی را برای AI ارسال یا ایندکس نمی‌کند. "
            "ایندکس‌گذاری فقط بعد از فعال‌سازی آزمایشی سرویس و انتخاب پیام‌های مجاز انجام می‌شود.\n"
            "هر زمان خواستی /memory_off بزن."
        )
    if command == "/memory_off":
        deleted = set_semantic_consent(db, user_id, False)
        return (
            f"اجازه حافظه معنایی لغو شد و {deleted} بردار مرتبط حذف شد ✅\n"
            "تاریخچه اصلی مکالمات همچنان باقی است. برای پاک کردن حافظه‌های استخراج‌شده /memory_clear را بزن."
        )
    if command == "/memory_clear":
        cleared = clear_derived_memory(db, user_id)
        return (
            "حافظه‌های استخراج‌شده از حساب تو پاک شدند ✅\n"
            f"بردارها: {cleared['vectors']} | خاطرات زمانی: {cleared['facts']} | "
            f"خاطرات قدیمی: {cleared['legacy']}\n"
            "این دستور پیام‌های اصلی تلگرام/دیتابیس چت و سوابق مالی را حذف نمی‌کند. "
            "تا وقتی تاریخچه خام موجود است، بخش‌هایی از آن ممکن است همچنان در گفت‌وگوی عادی استفاده شود."
        )
    return None
