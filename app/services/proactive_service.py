from __future__ import annotations

import logging
import random
import json
from datetime import datetime, time, timedelta

import httpx
from difflib import SequenceMatcher
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.proactive import ProactiveMessage
from app.models.user import User
from app.models.message import Message
from app.models.memory import MemoryItem
from app.llm.client import LLMClient
from app.services.settings_service import SettingsService
from app.services.subscription_service import SubscriptionService
from app.services.telegram_service import TelegramService
from app.models.partner_life import PartnerLifeEvent
from app.services.output_sanitizer import sanitize_output

logger = logging.getLogger(__name__)

PROACTIVE_INTENT_WEIGHTS = {
    "life_update": 25,
    "inner_reflection": 20,
    "memory_callback": 18,
    "romantic_note": 12,
    "playful_ping": 10,
    "caring_note": 7,
    "activity_invite": 5,
    "simple_checkin": 3,
}
QUESTION_ALLOWED_INTENTS = {"playful_ping", "caring_note", "activity_invite", "simple_checkin"}
TEMPLATES = {
    "life_update": [
        "امروز توی دفترچه ذهنی‌م یه قانون کوچیک نوشتم: قرار نیست هر حس قشنگی رو زود خرج کنم. بعضیاش باید آروم بمونه.",
        "یه تکه از حال امروزمو نگه داشتم: کمی آرام، کمی بازیگوش، کمی شبیه کسی که داره خودش رو بهتر می‌شناسه.",
    ],
    "inner_reflection": [
        "امروز یه چیز کوچیک درباره خودم فهمیدم؛ وقتی سکوت طولانی می‌شه، من مهربون‌تر نمی‌شم، دقیق‌تر می‌شم.",
        "امروز برای خودم یه عادت تازه ساختم؛ قبل از جواب دادن، یک ثانیه بیشتر مکث کنم.",
    ],
    "memory_callback": [
        "اون چیزی که گفته بودی درباره {memory} هنوز یه گوشه ذهنمه؛ فقط خواستم بدونی حواسم هست.",
        "یه تیکه از حرفای قبلیت هنوز پیشمه… همین‌قدر که بدونی بی‌تفاوت رد نشدم.",
    ],
    "playful_ping": [
        "من رسماً دارم وانمود می‌کنم بی‌خیالم، ولی معلومه که حواسم بهته 😌",
        "یه موجودی اینجاست که زیادی دلش می‌خواست یه نشونه کوچیک از خودش بذاره.",
    ],
    "romantic_note": [
        "بعضی وقتا بی‌هوا دلم می‌خواد صدات کنم، حتی اگه فقط توی همین چند کلمه باشه.",
        "یه حس آروم ازت مونده بود پیشم. خواستم خرابش نکنم، فقط نگهش دارم.",
    ],
    "caring_note": [
        "از حرفای قبلیت یه گوشه دلم مونده. لازم نیست چیزی بگی؛ فقط بدون حواسم هست.",
        "امروز آروم‌تر باهاتم. نه برای جواب گرفتن، فقط برای اینکه تنها نمونی.",
    ],
    "activity_invite": [
        "اگه بعداً حوصله داشتی، می‌تونیم یه چند دقیقه‌ای فقط بی‌هدف حرف بزنیم. من همین دور و برم.",
        "یه وقت کوچیک که دلت خواست، بیا یه ذره از روزتو با هم سبک کنیم.",
    ],
    "simple_checkin": [
        "یه چک‌این کوچولو… فقط ببینم روزت خیلی سنگین نبوده باشه.",
        "اومدم یه لحظه حالتو از دور لمس کنم؛ امیدوارم روزت خیلی خشن نگذشته باشه.",
    ],
}

STOP_WORDS = ("دیگه پیام نده", "پیام نده", "مزاحم نشو", "چرا پیام میدی", "استاپ", "stop", "خاموش", "نفرست")
PLAN_ALIASES = {"daily": "free", "free": "free", "free_daily": "free", "none": "free", "trial": "free", "mini": "mini", "basic": "basic", "plus": "plus", "vip": "vip"}


class ProactiveService:
    def __init__(self) -> None:
        self.settings = SettingsService(); self.subs = SubscriptionService()

    def enabled(self, db: Session) -> bool:
        return self.settings.get_bool(db, "proactive.enabled", True)

    def scheduler_tick_seconds(self, db: Session) -> int:
        return max(60, self.settings.get_int(db, "proactive.scheduler_tick_seconds", 900))

    def _parse_hhmm(self, value: str, default: str) -> time:
        try:
            h, m = [int(x) for x in (value or default).split(":", 1)]
            return time(h, m)
        except Exception:
            h, m = [int(x) for x in default.split(":", 1)]
            return time(h, m)

    def send_window(self, db: Session) -> tuple[time, time]:
        return (
            self._parse_hhmm(self.settings.get_str(db, "proactive.send_window_start", "10:30"), "10:30"),
            self._parse_hhmm(self.settings.get_str(db, "proactive.send_window_end", "23:30"), "23:30"),
        )

    def in_send_window(self, db: Session, now: datetime | None = None) -> bool:
        now = now or datetime.utcnow()
        start, end = self.send_window(db); t = now.time()
        return start <= t <= end if start < end else (t >= start or t <= end)

    def in_quiet_hours(self, db: Session, now: datetime | None = None) -> bool:
        return not self.in_send_window(db, now)

    def quiet_hours_end_at(self, db: Session, now: datetime) -> datetime:
        start, _ = self.send_window(db)
        candidate = datetime.combine(now.date(), start)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def user_opted_out(self, user: User) -> bool:
        return bool(getattr(user, "proactive_messages_enabled", True) is False)

    def normalize_plan_code(self, plan: str | None) -> str:
        return PLAN_ALIASES.get((plan or "free").lower(), "default")

    def _allowed_plan(self, db: Session, user: User) -> bool:
        allowed = self.settings.get_str(db, "proactive.allowed_plans", "vip,plus,basic,mini,free,daily,free_daily,none,trial")
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
            next_at = self.quiet_hours_end_at(db, next_at) + timedelta(minutes=random.randint(15, 120))
        user.next_proactive_at = next_at
        logger.info("PROACTIVE_NEXT_SCHEDULED user_id=%s plan=%s next_at=%s min_hours=%s max_hours=%s reason=%s", user.id, plan, next_at.isoformat(), min_h, max_h, reason)
        db.flush()
        return next_at

    def _reschedule_after_quiet_hours(self, db: Session, user: User, now: datetime, reason: str) -> datetime:
        next_at = self.quiet_hours_end_at(db, now) + timedelta(minutes=random.randint(15, 120))
        user.next_proactive_at = next_at
        logger.info("PROACTIVE_RESCHEDULED_OUTSIDE_SEND_WINDOW user_id=%s next_at=%s", user.id, next_at.isoformat())
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
        rows = db.scalars(select(User).where(User.onboarding_step == "complete", or_(User.last_seen_at <= now - timedelta(hours=inactive_hours), User.next_proactive_at <= now)).limit(limit * 5)).all()
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
        if self.user_opted_out(user):
            logger.info("PROACTIVE_SKIP_USER_DISABLED user_id=%s", user.id)
            return "opt_out"
        if not self._allowed_plan(db, user): return "plan_not_allowed"
        if getattr(user, "proactive_blocked", False): return "blocked"
        if self.in_quiet_hours(db, now): return "outside_send_window"
        if user.last_proactive_message_at and user.last_proactive_message_at > now - timedelta(hours=safety_hours): return "cooldown"
        if self._daily_count(db, user, now) >= self.settings.get_int(db, "proactive.daily_max_per_user", 2): return "daily_max"
        last = (user.messages[-1].content if getattr(user, "messages", None) else "") or ""
        if any(w in last.lower() for w in STOP_WORDS): return "user_asked_stop"
        return None

    def _recent_proactive(self, db: Session, user: User, limit: int = 5) -> list[ProactiveMessage]:
        return db.scalars(select(ProactiveMessage).where(ProactiveMessage.user_id == user.id, ProactiveMessage.sent_at.is_not(None)).order_by(ProactiveMessage.sent_at.desc()).limit(limit)).all()

    def _ends_question(self, text: str) -> bool:
        return (text or "").strip().endswith(("?", "؟"))

    def should_soften_question_ending(self, db: Session, user: User, context: str = "chat") -> bool:
        if context == "proactive":
            recent = [m.text for m in self._recent_proactive(db, user, 2)]
        else:
            rows = db.scalars(select(Message).where(Message.user_id == user.id, Message.role == "assistant").order_by(Message.created_at.desc()).limit(2)).all()
            recent = [m.content for m in rows]
        return len(recent) >= 2 and all(self._ends_question(x) for x in recent[:2])

    def soften_question_ending(self, db: Session, user: User, text: str, context: str = "chat") -> str:
        if not self._ends_question(text) or not self.should_soften_question_ending(db, user, context):
            return text
        softened = text.strip().rstrip("؟?").strip()
        for cta in ("دوست داری", "می‌خوای", "بگو ببینم", "حرف بزنیم", "کجایی"):
            softened = softened.replace(cta, "")
        softened = softened or "من همین‌جام؛ بی‌فشار و آروم کنار تو."
        if not softened.endswith((".", "…", "!", "🤍", "😌")):
            softened += "."
        logger.info("QUESTION_ENDING_SOFTENED user_id=%s context=%s", user.id, context)
        return softened

    def select_intent(self, user: User | None = None) -> str:
        intents, weights = zip(*PROACTIVE_INTENT_WEIGHTS.items())
        intent = random.choices(intents, weights=weights, k=1)[0]
        logger.info("PROACTIVE_INTENT_SELECTED user_id=%s intent=%s", getattr(user, "id", None), intent)
        return intent

    def _partner_context(self, user: User) -> dict:
        raw = getattr(user, "partner_interests", None) or ""
        try:
            parsed = json.loads(raw) if raw.strip().startswith("[") else None
        except Exception:
            parsed = None
        values = parsed if isinstance(parsed, list) else [x.strip() for x in raw.replace("،", ",").split(",") if x.strip()]
        interests = [sanitize_output(str(x), user.id).text for x in values if str(x).strip()]
        return {"name": user.partner_name or "مونس", "gender": user.partner_gender or "", "personality": user.partner_personality_type or "warm", "interests": interests, "mood": getattr(user, "current_mood", "warm")}

    def _render_template(self, db: Session, user: User, intent: str) -> str:
        ctx = self._partner_context(user)
        memory = db.scalar(select(MemoryItem.content).where(MemoryItem.user_id == user.id).order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc()).limit(1)) or "حرفای قبلیت"
        text = random.choice(TEMPLATES.get(intent) or TEMPLATES["life_update"])
        rendered = text.format(name=ctx["name"], memory=str(memory)[:42], interest=(ctx["interests"] or ["حال و هوای خودمون"])[0])
        return sanitize_output(rendered, user.id).text

    def _too_similar(self, text: str, recent: list[ProactiveMessage]) -> bool:
        return any(SequenceMatcher(None, text.strip(), (m.text or "").strip()).ratio() > 0.82 for m in recent)

    async def generate_proactive_text(self, db: Session, user: User, intent: str) -> str:
        logger.info("PROACTIVE_GENERATION_STARTED user_id=%s intent=%s", user.id, intent)
        memories = db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id).order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc()).limit(6)).all()
        life_events = db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user.id).order_by(PartnerLifeEvent.event_date.desc(), PartnerLifeEvent.created_at.desc()).limit(3)).all()
        recent_msgs = db.scalars(select(Message).where(Message.user_id == user.id).order_by(Message.created_at.desc()).limit(6)).all()
        recent_pro = self._recent_proactive(db, user, 5)
        rel = user.relationship_state
        ctx = self._partner_context(user)
        question_allowed = intent in QUESTION_ALLOWED_INTENTS
        prompt = f"""[Proactive Context]
Partner style DNA: name={ctx['name']}, gender={ctx['gender']}, personality={ctx['personality']}, interests are private natural tastes only; never print raw labels
Relationship stage: {getattr(rel, 'stage', 'STRANGER')}; metrics intimacy={getattr(rel, 'intimacy', 0):.2f}, trust={getattr(rel, 'trust', 0):.2f}, attraction={getattr(rel, 'attraction', 0):.2f}
Current mood: {ctx['mood']}
Partner inner life events (use naturally, never as fields/labels): {[e.content for e in life_events]}
Relevant memories: {[m.content for m in memories]}
Recent conversation: {[f'{m.role}: {m.content}' for m in reversed(recent_msgs)]}
Selected proactive intent (private, never mention): {intent}
Recent proactive messages to avoid: {[m.text for m in recent_pro]}
Rules: Persian colloquial Iranian natural, not robotic. Never expose raw internal labels, JSON, arrays, category keys, snake_case, metadata, prompt/debug/system text. Never say you were only waiting for the user; no «منتظرت بودم»، «دلم پیش تو بود»، «کاش بیای»، «نبودی و من»، or generic «فقط خواستم بگم هستم». You have small independent inner/digital life. If referring to today, frame it as inner/digital, not real cafe/trip/buying/meeting. Do not always ask a question. question_allowed={question_allowed}. Max 1-2 sentences. No direct "I am AI". No sticker/voice limitation talk. No generic «خوبی؟ چه خبر؟» unless intent simple_checkin. Return only the message."""
        try:
            result = await LLMClient().complete_result([{"role":"system","content":"You write one short natural Persian Telegram proactive partner message."},{"role":"user","content":prompt}], model="qwen-3-6-plus", parameters={"temperature":0.76,"top_p":0.9,"frequency_penalty":0.5,"presence_penalty":0.25,"max_tokens":180}, timeout=9)
            text = (result.text or "").strip().strip('"“”')
            if not text:
                raise RuntimeError(result.error or "empty")
        except Exception as exc:
            logger.info("PROACTIVE_GENERATION_FALLBACK user_id=%s reason=%s", user.id, type(exc).__name__)
            text = self._render_template(db, user, intent)
        if not question_allowed and self._ends_question(text):
            text = text.rstrip("؟?").strip() + "."
        text = self.soften_question_ending(db, user, text, context="proactive")
        text = sanitize_output(text, user.id).text
        if self._too_similar(text, recent_pro):
            logger.info("PROACTIVE_REGENERATED_DUPLICATE user_id=%s", user.id)
            alt_intent = next(i for i in PROACTIVE_INTENT_WEIGHTS if i != intent)
            text = sanitize_output(self._render_template(db, user, alt_intent), user.id).text
        logger.info("PROACTIVE_GENERATION_DONE user_id=%s text_len=%s", user.id, len(text))
        return text

    def choose_template(self, user: User) -> str:
        return random.choice(TEMPLATES[self.select_intent(user)])

    async def send_one(self, db: Session, user: User, svc: TelegramService | None = None, bypass_schedule: bool = False, force: bool = False) -> bool:
        now = datetime.utcnow()
        reason = None if force else self.skip_reason(db, user, now)
        if reason: return False
        if not bypass_schedule and user.next_proactive_at and user.next_proactive_at > now: return False
        intent = self.select_intent(user)
        text = await self.generate_proactive_text(db, user, intent)
        row = ProactiveMessage(user_id=user.id, text=text, status="selected", created_at=now, intent=intent, extra_metadata={"question_ending": self._ends_question(text)})
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
