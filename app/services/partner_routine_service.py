from __future__ import annotations

import json
import logging
from datetime import date, time
from typing import Any
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.partner_life import PartnerDailyRoutine
from app.models.settings import AppSetting

logger = logging.getLogger(__name__)

SLOTS = {
    "morning": (7, 12),
    "afternoon": (12, 18),
    "evening": (18, 22),
    "late_night": (22, 7),
}

class PartnerRoutineService:
    prompt_version = "routine_v1"

    def default_city(self, db: Session) -> str:
        try:
            return db.scalar(select(AppSetting.value).where(AppSetting.key == "roleplay.default_city")) or "تهران"
        except Exception:
            return "تهران"

    def deterministic_schedule(self, user: Any, local_date: date, city: str) -> dict[str, dict[str, str]]:
        cafes = ["کافه‌ی کوچیک نزدیک ولیعصر", "کافه‌ای حوالی انقلاب", "یه کافه آروم نزدیک خونه"]
        walks = ["قدم زدن کوتاه توی محله", "خرید چند چیز ساده از سوپر", "رد شدن از کنار مغازه‌های شلوغ"]
        seed = (getattr(user, "id", 0) or 0) + local_date.toordinal()
        cafe = cafes[seed % len(cafes)]
        walk = walks[(seed // 2) % len(walks)]
        mood = getattr(user, "current_mood", "warm") or "warm"
        return {
            "morning": {"activity":"چای درست کردن و جمع‌وجور کردن صبح", "location":f"خانه در {city}", "energy":"آرام و بیدار", "social_context":"تنها", "shareable_detail":"چای تازه دم کردم و پنجره رو باز گذاشتم.", "outfit_or_visual_detail":"لباس راحت خونه", "starts_at_local":"07:00", "ends_at_local":"12:00"},
            "afternoon": {"activity":walk, "location":f"محله‌ای در {city}", "energy":"سرحال" if mood != "low" else "آروم", "social_context":"بین مردم شهر", "shareable_detail":f"برای چند خرید کوچیک رفتم بیرون و هوای {city} رو حس کردم.", "outfit_or_visual_detail":"مانتوی ساده و کیف سبک", "starts_at_local":"12:00", "ends_at_local":"18:00"},
            "evening": {"activity":"نشستن در کافه و گوش دادن به موسیقی", "location":cafe, "energy":"نرم و صمیمی", "social_context":"فضای خلوت کافه", "shareable_detail":"یه آهنگ آروم گذاشته بودن و من کنار پنجره نشسته بودم.", "outfit_or_visual_detail":"یه شال خوش‌رنگ", "starts_at_local":"18:00", "ends_at_local":"22:00"},
            "late_night": {"activity":"استراحت در خانه", "location":f"خانه در {city}", "energy":"خواب‌آلود و نزدیک", "social_context":"تنها", "shareable_detail":"چراغ اتاق کم‌نوره و دارم آروم می‌شم برای خواب.", "outfit_or_visual_detail":"لباس خواب راحت", "starts_at_local":"22:00", "ends_at_local":"07:00"},
        }

    def get_or_create_for_context(self, db: Session, user: Any, time_context) -> PartnerDailyRoutine:
        existing = db.scalar(select(PartnerDailyRoutine).where(PartnerDailyRoutine.user_id == user.id, PartnerDailyRoutine.local_date == time_context.local_date))
        if existing:
            logger.info("PARTNER_ROUTINE_REUSED user_id=%s timezone=%s local_hour=%s gap_bucket=%s", user.id, time_context.timezone_name, time_context.local_hour, time_context.gap_bucket)
            return existing
        city = self.default_city(db)
        schedule = self.deterministic_schedule(user, time_context.local_date, city)
        row = PartnerDailyRoutine(user_id=user.id, local_date=time_context.local_date, timezone_name=time_context.timezone_name, city=city, schedule_json=json.dumps(schedule, ensure_ascii=False), source="deterministic", prompt_version=self.prompt_version)
        db.add(row)
        try:
            db.flush()
            logger.info("PARTNER_ROUTINE_CREATED user_id=%s timezone=%s local_hour=%s gap_bucket=%s slot_name=%s", user.id, time_context.timezone_name, time_context.local_hour, time_context.gap_bucket, self.slot_name(time_context.local_hour))
            return row
        except IntegrityError:
            db.rollback()
            row = db.scalar(select(PartnerDailyRoutine).where(PartnerDailyRoutine.user_id == user.id, PartnerDailyRoutine.local_date == time_context.local_date))
            if row:
                logger.info("PARTNER_ROUTINE_REUSED user_id=%s timezone=%s local_hour=%s gap_bucket=%s", user.id, time_context.timezone_name, time_context.local_hour, time_context.gap_bucket)
                return row
            raise

    def slot_name(self, hour: int) -> str:
        if 7 <= hour < 12: return "morning"
        if 12 <= hour < 18: return "afternoon"
        if 18 <= hour < 22: return "evening"
        return "late_night"

    def current_slot(self, routine: PartnerDailyRoutine, time_context) -> dict[str, Any]:
        data = json.loads(routine.schedule_json or "{}")
        name = self.slot_name(time_context.local_hour)
        slot = dict(data.get(name) or data.get("late_night") or {})
        slot["slot_name"] = name
        logger.info("PARTNER_ROUTINE_SLOT_SELECTED user_id=%s timezone=%s local_hour=%s gap_bucket=%s slot_name=%s", routine.user_id, time_context.timezone_name, time_context.local_hour, time_context.gap_bucket, name)
        return slot

    def continuity_detail(self, routine: PartnerDailyRoutine, current_slot: dict[str, Any]) -> str:
        return str(current_slot.get("shareable_detail") or current_slot.get("activity") or "").strip()
