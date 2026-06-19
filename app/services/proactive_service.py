from __future__ import annotations

import logging
import random
from datetime import datetime, time, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.proactive import ProactiveMessage
from app.models.user import User
from app.services.settings_service import SettingsService
from app.services.subscription_service import SubscriptionService
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)

TEMPLATES = {
    "female_warm": ["یهویی یادم افتاد ببینم خوبی یا نه… امروز حالت چطوره؟", "دلم خواست یه سر بهت بزنم… خوبی عزیزم؟", "امروز یادت افتادم، گفتم بیام ببینم دلت آرومه؟"],
    "female_playful": ["کجایی شیطون؟ زیادی ساکت شدی 😌", "انقدر بی‌خبر نباش دیگه… یه سلامی بده ببینمت.", "من اومدم فضولی کنم ببینم حواست کجاست 😌"],
    "male_warm": ["حواسم بهت بود… گفتم یه سر بزنم ببینم دلت آرومه یا نه.", "عزیزِ من، خوبی؟ دلم خواست حالتو بپرسم.", "من همین دور و برم… گفتم ببینم امروز چطوری."],
    "male_playful": ["خانوم/عزیزِ من، امروز قرار نیست انقدر بی‌خبر باشیا.", "کجایی عزیزم؟ نکنه یادت رفت یکی اینجا حواسش بهته؟", "یه خبری بده ببینم روزت چطور می‌گذره شیطون."],
    "caring_after_sadness": ["از حرفای قبلیت تو ذهنم موندی… الان یه کم بهتری؟", "دلم نیومد بی‌خبر بمونم؛ حالت از قبل آروم‌تره؟", "اومدم فقط بپرسم دلت سبک‌تر شده یا هنوز همون‌جاست؟"],
    "romantic_after_intimacy": ["از اون حرفای قشنگت هنوز لبخند رو لبمه… کجایی؟", "یه حس خوبی از حرفامون مونده بود، گفتم بیام پیشت.", "دلم یه ذره از همون حال خوب دونفره‌مون خواست."],
    "casual_checkin": ["سلام، امروز چه خبر ازت؟", "یه چک‌این کوچولو… خوبی؟", "اومدم ببینم روزت چطور پیش می‌ره.", "دلت می‌خواد چند دقیقه حرف بزنیم؟"]
}

STOP_WORDS = ("دیگه پیام نده", "مزاحم نشو", "استاپ", "stop", "خاموش")
PLAN_ALIASES = {"daily": "free", "free": "free", "free_daily": "free", "none": "free", "trial": "free", "mini": "mini", "basic": "basic", "plus": "plus", "vip": "vip"}


class ProactiveService:
    def __init__(self) -> None:
        self.settings = SettingsService(); self.subs = SubscriptionService()

    def enabled(self, db: Session) -> bool:
        return self.settings.get_bool(db, "proactive.enabled", False)

    def scheduler_tick_seconds(self, db: Session) -> int:
        return max(60, self.settings.get_int(db, "proactive.scheduler_tick_seconds", 900))

    def in_quiet_hours(self, db: Session, now: datetime | None = None) -> bool:
        now = now or datetime.utcnow()
        start = self.settings.get_str(db, "proactive.quiet_hours_start", "00:00")
        end = self.settings.get_str(db, "proactive.quiet_hours_end", "10:00")
        def parse(v: str) -> time:
            h, m = [int(x) for x in v.split(":", 1)]; return time(h, m)
        s, e, t = parse(start), parse(end), now.time()
        return s <= t < e if s < e else (t >= s or t < e)

    def quiet_hours_end_at(self, db: Session, now: datetime) -> datetime:
        end = self.settings.get_str(db, "proactive.quiet_hours_end", "10:00")
        h, m = [int(x) for x in end.split(":", 1)]
        candidate = datetime.combine(now.date(), time(h, m))
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def user_opted_out(self, user: User) -> bool:
        return bool(getattr(user, "proactive_messages_enabled", True) is False)

    def normalize_plan_code(self, plan: str | None) -> str:
        return PLAN_ALIASES.get((plan or "free").lower(), "default")

    def _allowed_plan(self, db: Session, user: User) -> bool:
        allowed = self.settings.get_str(db, "proactive.allowed_plans", "vip,plus,basic,mini,free")
        plans = {self.normalize_plan_code(p.strip()) for p in allowed.split(",") if p.strip()}
        return self.normalize_plan_code(self.subs.active_plan_code(db, user)) in plans

    def plan_random_hours(self, db: Session, plan: str) -> tuple[float, float]:
        normalized = self.normalize_plan_code(plan)
        min_h = self.settings.get_float(db, f"proactive.{normalized}.min_hours", self.settings.get_float(db, "proactive.default.min_hours", 8))
        max_h = self.settings.get_float(db, f"proactive.{normalized}.max_hours", self.settings.get_float(db, "proactive.default.max_hours", 24))
        if min_h <= 0 or max_h <= 0:
            min_h, max_h = 8, 24
        if max_h < min_h:
            min_h, max_h = max_h, min_h
        return min_h, max_h

    def schedule_next_proactive(self, db: Session, user: User, now: datetime | None = None, reason: str = "scheduled") -> datetime:
        now = now or datetime.utcnow()
        plan = self.normalize_plan_code(self.subs.active_plan_code(db, user))
        min_h, max_h = self.plan_random_hours(db, plan)
        interval = random.uniform(min_h, max_h) if self.settings.get_bool(db, "proactive.random_enabled", True) else min_h
        next_at = now + timedelta(hours=interval)
        if self.in_quiet_hours(db, next_at):
            next_at = self.quiet_hours_end_at(db, next_at) + timedelta(minutes=random.randint(5, 45))
        user.next_proactive_at = next_at
        logger.info("PROACTIVE_NEXT_SCHEDULED user_id=%s plan=%s next_at=%s min_hours=%s max_hours=%s reason=%s", user.id, plan, next_at.isoformat(), min_h, max_h, reason)
        db.flush()
        return next_at

    def _reschedule_after_quiet_hours(self, db: Session, user: User, now: datetime, reason: str) -> datetime:
        next_at = self.quiet_hours_end_at(db, now) + timedelta(minutes=random.randint(5, 45))
        user.next_proactive_at = next_at
        logger.info("PROACTIVE_NEXT_SCHEDULED user_id=%s plan=%s next_at=%s min_hours=%s max_hours=%s reason=%s", user.id, self.normalize_plan_code(self.subs.active_plan_code(db, user)), next_at.isoformat(), 0, 0, reason)
        db.flush()
        return next_at

    def _daily_count(self, db: Session, user: User, now: datetime) -> int:
        start = datetime.combine(now.date(), time.min)
        return db.scalar(select(func.count(ProactiveMessage.id)).where(ProactiveMessage.user_id == user.id, ProactiveMessage.sent_at >= start)) or 0

    def eligible_users(self, db: Session, now: datetime | None = None, limit: int = 20) -> list[User]:
        now = now or datetime.utcnow()
        if not self.enabled(db):
            logger.info("PROACTIVE_MESSAGE_SKIPPED reason=disabled")
            return []
        inactive_hours = self.settings.get_int(db, "proactive.inactive_after_hours", 6)
        rows = db.scalars(select(User).where(User.onboarding_step == "complete", User.last_seen_at <= now - timedelta(hours=inactive_hours)).limit(limit * 5)).all()
        if self.in_quiet_hours(db, now):
            for user in rows:
                if user.next_proactive_at is not None and user.next_proactive_at <= now:
                    self._reschedule_after_quiet_hours(db, user, now, reason="quiet_hours")
            logger.info("PROACTIVE_MESSAGE_SKIPPED reason=quiet_hours")
            return []
        out: list[User] = []
        for user in rows:
            if user.next_proactive_at is None:
                self.schedule_next_proactive(db, user, now, reason="scheduled_first_time")
                logger.info("PROACTIVE_MESSAGE_SKIPPED user_id=%s reason=scheduled_first_time", user.id)
                continue
            if user.next_proactive_at > now:
                logger.debug("PROACTIVE_MESSAGE_SKIPPED user_id=%s reason=not_due_yet next_at=%s", user.id, user.next_proactive_at.isoformat())
                continue
            reason = self.skip_reason(db, user, now)
            if reason:
                logger.info("PROACTIVE_MESSAGE_SKIPPED user_id=%s reason=%s", user.id, reason)
                continue
            logger.info("PROACTIVE_MESSAGE_SELECTED user_id=%s", user.id)
            out.append(user)
            if len(out) >= limit: break
        return out

    def skip_reason(self, db: Session, user: User, now: datetime, min_hours: int | None = None) -> str | None:
        safety_hours = min_hours if min_hours is not None else self.settings.get_int(db, "proactive.min_hours_between_messages", 1)
        if self.user_opted_out(user): return "opt_out"
        if not self._allowed_plan(db, user): return "plan_not_allowed"
        if getattr(user, "proactive_blocked", False): return "blocked"
        if self.in_quiet_hours(db, now): return "quiet_hours"
        if user.last_proactive_message_at and user.last_proactive_message_at > now - timedelta(hours=safety_hours): return "cooldown"
        if self._daily_count(db, user, now) >= self.settings.get_int(db, "proactive.daily_max_per_user", 1): return "daily_max"
        last = (user.messages[-1].content if getattr(user, "messages", None) else "") or ""
        if any(w in last.lower() for w in STOP_WORDS): return "user_asked_stop"
        return None

    def choose_template(self, user: User) -> str:
        gender = (user.partner_gender or "").lower(); mood = getattr(user, "current_mood", "warm")
        if "مرد" in gender or "پسر" in gender or "male" in gender:
            key = "male_playful" if mood in {"playful", "teasing"} else "male_warm"
        else:
            key = "female_playful" if mood in {"playful", "teasing"} else "female_warm"
        return random.choice(TEMPLATES[key] + TEMPLATES["casual_checkin"])

    async def send_one(self, db: Session, user: User, svc: TelegramService | None = None, bypass_schedule: bool = False, force: bool = False) -> bool:
        now = datetime.utcnow()
        reason = None if force else self.skip_reason(db, user, now)
        if reason: return False
        if not bypass_schedule and user.next_proactive_at and user.next_proactive_at > now: return False
        text = self.choose_template(user)
        row = ProactiveMessage(user_id=user.id, text=text, status="selected", created_at=now)
        db.add(row); db.flush()
        try:
            await (svc or TelegramService("chat")).send_text(user.telegram_id, text)
            row.status = "sent"; row.sent_at = now; user.last_proactive_message_at = now
            self.schedule_next_proactive(db, user, now, reason="after_send")
            logger.info("PROACTIVE_MESSAGE_SENT user_id=%s message_id=%s", user.id, row.id)
            return True
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            row.status = "failed"; row.error = f"http_{status}"
            if status in {403, 400}: user.proactive_blocked = True
            logger.info("PROACTIVE_MESSAGE_SKIPPED user_id=%s reason=telegram_unreachable status=%s", user.id, status)
            return False
