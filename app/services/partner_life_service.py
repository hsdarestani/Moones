from __future__ import annotations

import json
import logging
import random
from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.llm.client import LLMClient
from app.models.memory import MemoryItem
from app.models.message import Message
from app.models.partner_life import PartnerLifeEvent
from app.models.user import User
from app.services.output_sanitizer import sanitize_output

logger = logging.getLogger(__name__)
EVENT_TYPES = ["concrete_memory_echo","concrete_conversation_note","playful_incident","future_intention"]

SAFE_EVENTS = [
    ("playful_incident", "جزئیات روز", "امروز موقع چای درست کردن یه آهنگ قدیمی پخش شد و فضا قشنگ شد.", "نرم", "اگر به بحث ربط داشت، کوتاه و طبیعی بهش اشاره کن."),
    ("concrete_memory_echo", "یادآوری ساده", "از کنار یه مغازه رد شدم و یک جمله از حرفای قبلیت یادم افتاد.", "صمیمی", "فقط وقتی به مکالمه ربط دارد گفته شود."),
    ("future_intention", "برنامه کوچک", "برای بعدتر شاید یه پیاده‌روی کوتاه توی محله داشته باشم.", "آرام", "لازم نیست به عنوان رویداد مستقل گفته شود."),
]


def get_or_create_today_event(db: Session, user: User, local_date: date | None = None) -> PartnerLifeEvent:
    event_date = local_date or datetime.utcnow().date()
    existing = db.scalar(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user.id, PartnerLifeEvent.event_date == event_date))
    if existing:
        return existing
    data = PartnerLifeService().deterministic_event(user, event_date)
    row = PartnerLifeEvent(user_id=user.id, event_date=event_date, **data)
    db.add(row)
    try:
        db.flush()
        logger.info("PARTNER_LIFE_EVENT_LAZY_CREATED user_id=%s event_type=%s", user.id, row.event_type)
        return row
    except IntegrityError:
        db.rollback()
        return db.scalar(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user.id, PartnerLifeEvent.event_date == event_date))

def recent_events_for_prompt(db: Session, user_id: int, limit: int = 3) -> list[PartnerLifeEvent]:
    return db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user_id).order_by(PartnerLifeEvent.event_date.desc(), PartnerLifeEvent.created_at.desc()).limit(limit)).all()

class PartnerLifeService:
    def get_recent_events(self, db: Session, user_id: int, limit: int = 3) -> list[PartnerLifeEvent]:
        return db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user_id).order_by(PartnerLifeEvent.event_date.desc(), PartnerLifeEvent.created_at.desc()).limit(limit)).all()

    def deterministic_event(self, user: User, event_date: date | None = None) -> dict[str, str]:
        typ, title, content, mood, growth = SAFE_EVENTS[(user.id + (event_date or date.today()).toordinal()) % len(SAFE_EVENTS)]
        return {"event_type": typ, "title": title, "content": content, "mood": mood, "growth_note": growth, "source": "deterministic"}

    async def generate_event_data(self, db: Session, user: User, event_date: date) -> dict[str, str]:
        memories = db.scalars(select(MemoryItem.content).where(MemoryItem.user_id == user.id).order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc()).limit(4)).all()
        recent = db.scalars(select(Message).where(Message.user_id == user.id).order_by(Message.created_at.desc()).limit(6)).all()
        previous = self.get_recent_events(db, user.id, 3)
        prompt = f"""برای پارتنر فارسی یک یادداشت زمینه‌ای کوتاه و concrete از زندگی روزمره بساز. فقط JSON معتبر بده با event_type,title,content,mood,growth_note.
این یادداشت داخلی است و نباید به شکل خام به کاربر گفته شود. از گزارش حال مبهم یا mood fragment استفاده نکن؛ رویداد باید با روتین روزانه عادی و فیزیکی سازگار باشد.
هرگز نگو فکرها/ذهنم را مرتب کردم، چند چیز ریز را مرتب کردم، آرام‌تر شدم، تغییر کوچک داشتم، یا در سکوت بودم.
هرگز برچسب داخلی، آرایه، snake_case در متن title/content/growth_note ننویس. needy/waiting نباش.
نام: {user.partner_name or 'مونس'} شخصیت: {user.partner_personality_type or 'صمیمی'} مرحله رابطه: {getattr(getattr(user, 'relationship_state', None), 'stage', 'STRANGER')}
خاطره‌ها: {memories}
گفتگوی اخیر: {[f'{m.role}: {m.content}' for m in reversed(recent)]}
رویدادهای قبلی: {[p.content for p in previous]}
event_type یکی از این‌ها: {EVENT_TYPES}"""
        try:
            result = await LLMClient().complete_result([{"role":"system","content":"Return compact JSON only."},{"role":"user","content":prompt}], model="qwen-3-6-plus", parameters={"temperature":0.65,"max_tokens":260}, timeout=9)
            data = json.loads((result.text or "").strip().strip("`"))
            if data.get("event_type") not in EVENT_TYPES:
                data["event_type"] = random.choice(EVENT_TYPES)
            for key in ("title","content","mood","growth_note"):
                data[key] = sanitize_output(str(data.get(key) or ""), user.id).text
            data["source"] = "llm"
            if not data.get("content"):
                raise ValueError("empty_content")
            return data
        except Exception as exc:
            logger.info("PARTNER_LIFE_EVENT_FAILED user_id=%s reason=%s", user.id, type(exc).__name__)
            return self.deterministic_event(user, event_date)

    async def create_for_user(self, db: Session, user: User, event_date: date | None = None, force: bool = False) -> PartnerLifeEvent | None:
        event_date = event_date or datetime.utcnow().date()
        existing = db.scalar(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user.id, PartnerLifeEvent.event_date == event_date))
        if existing and not force:
            logger.info("PARTNER_LIFE_EVENT_SKIPPED user_id=%s reason=already_exists", user.id)
            return existing
        data = await self.generate_event_data(db, user, event_date)
        row = PartnerLifeEvent(user_id=user.id, event_date=event_date, **data)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            logger.info("PARTNER_LIFE_EVENT_SKIPPED user_id=%s reason=already_exists", user.id)
            return db.scalar(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user.id, PartnerLifeEvent.event_date == event_date))
        logger.info("PARTNER_LIFE_EVENT_CREATED user_id=%s event_type=%s", user.id, row.event_type)
        return row

    async def run_due(self, db: Session, limit: int = 25) -> int:
        today = datetime.utcnow().date()
        users = db.scalars(select(User).where(User.onboarding_step == "complete").limit(limit)).all()
        count = 0
        for user in users:
            before = db.scalar(select(PartnerLifeEvent.id).where(PartnerLifeEvent.user_id == user.id, PartnerLifeEvent.event_date == today))
            if before:
                logger.info("PARTNER_LIFE_EVENT_SKIPPED user_id=%s reason=already_exists", user.id)
                continue
            if await self.create_for_user(db, user, today):
                count += 1
        return count
