from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analytics import AnalyticsEvent

LOW_WALLET_COOLDOWN_SECONDS = 120


def recharge_keyboard(text: str = "شارژ کیف پول") -> dict:
    from app.core.config import get_settings
    settings = get_settings()
    username = (settings.management_bot_username or settings.telegram_management_bot_username).lstrip("@")
    url = settings.management_bot_url or f"https://t.me/{username}"
    return {"inline_keyboard": [[{"text": text, "url": url}]]}


def chat_insufficient_text(*, balance: int, required: int) -> str:
    return (
        "سکه‌ات برای این پیام کافی نیست 🌙\n"
        f"موجودی فعلی: {balance} سکه\n"
        f"هزینه تقریبی این پیام: {required} سکه\n"
        "از کیف پول مونس می‌تونی شارژش کنی."
    )


def feature_insufficient_text(feature: str, *, balance: int, required: int) -> str:
    labels = {
        "image_generation_bundle": "دریافت عکس",
        "image_generation": "دریافت عکس",
        "stt": "تبدیل وویس",
        "vision": "دیدن عکس",
        "tts": "فرستادن وویس",
        "chat": "این پیام",
    }
    label = labels.get(feature, "این درخواست")
    return f"موجودی سکه‌ات برای {label} کافی نیست 🌙\nموجودی فعلی: {balance} سکه\nهزینه تقریبی: {required} سکه\nاز کیف پول مونس می‌تونی شارژش کنی."


def record_insufficient_event(db: Session, *, user_id: int, feature: str, required: int, balance: int, telegram_update_id: int | None = None) -> AnalyticsEvent:
    event = AnalyticsEvent(
        user_id=user_id,
        event_type="chat_blocked_insufficient_coins" if feature == "chat" else f"{feature}_blocked_insufficient_coins",
        event_date=datetime.utcnow(),
        metadata_json={
            "internal_user_id": user_id,
            "required_coins": required,
            "balance": balance,
            "feature": feature,
            "timestamp": datetime.utcnow().isoformat(),
            "telegram_update_id": telegram_update_id,
        },
    )
    db.add(event)
    db.flush()
    return event


def should_send_low_wallet_notice(db: Session, *, user_id: int, feature: str, dedupe_key: str | None = None, cooldown_seconds: int = LOW_WALLET_COOLDOWN_SECONDS) -> bool:
    since = datetime.utcnow() - timedelta(seconds=cooldown_seconds)
    event_type = "low_wallet_notice_sent"
    stmt = select(AnalyticsEvent).where(
        AnalyticsEvent.user_id == user_id,
        AnalyticsEvent.event_type == event_type,
        AnalyticsEvent.event_date >= since,
    )
    recent = db.scalars(stmt).all()
    for ev in recent:
        meta = ev.metadata_json or {}
        if dedupe_key and meta.get("dedupe_key") == dedupe_key:
            return False
        if meta.get("feature") == feature:
            return False
    db.add(AnalyticsEvent(user_id=user_id, event_type=event_type, event_date=datetime.utcnow(), metadata_json={"feature": feature, "dedupe_key": dedupe_key}))
    db.flush()
    return True
