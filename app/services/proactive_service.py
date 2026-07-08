from __future__ import annotations

import logging
import random
import json
from datetime import datetime, time, timedelta

import httpx
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
from app.services.partner_life_service import get_or_create_today_event, recent_events_for_prompt
from app.services.partner_autonomy_policy import violates_autonomy_policy, safe_autonomous_fallback
from app.services.outbound_text_policy import sanitize_user_facing_text
from app.services.proactive_policy import ProactiveCandidate, choose_proactive_variant, proactive_allowed_for_recent_user_messages, proactive_similarity, references_context, should_send_proactive, validate_proactive_text

logger = logging.getLogger(__name__)

PROACTIVE_INTENT_WEIGHTS = {"simple_checkin": 45, "light_presence": 35, "specific_reply_followup": 20}
QUESTION_ALLOWED_INTENTS = {"simple_checkin", "light_presence", "specific_reply_followup"}
TEMPLATES = {
    "simple_checkin": ["سرت شلوغه؟", "امروز حالت چطوره؟", "الان وقت حرف زدن داری؟", "امروزت چطور بود؟", "چند دقیقه وقت داری حرف بزنیم؟", "امروز خیلی درگیر بودی؟", "همه‌چی اوکیه؟"],
    "light_presence": ["یه سر زدم ببینم هستی یا نه.", "الان وقت حرف زدن داری؟", "حوصله داری یه کم گپ بزنیم؟", "چند دقیقه وقت داری حرف بزنیم؟", "کجایی این روزا؟"],
    "specific_reply_followup": ["اون کاری که گفتی به کجا رسید؟", "رسیدی؟", "اون بازارچه چطور شد؟"],
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
        rows = db.scalars(
            select(User)
            .where(
                User.onboarding_step == "complete",
                User.proactive_blocked == False,
                or_(User.proactive_messages_enabled == True, User.proactive_messages_enabled.is_(None)),
                or_(
                    User.last_seen_at <= now - timedelta(hours=inactive_hours),
                    User.next_proactive_at <= now,
                ),
            )
            .order_by(User.next_proactive_at.asc().nulls_last(), User.last_seen_at.asc())
            .limit(limit * 5)
        ).all()
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
        return any(proactive_similarity(text, m.text or "") > 0.82 for m in recent)

    def _recent_user_texts(self, db: Session, user: User, limit: int = 5) -> list[str]:
        rows = db.scalars(select(Message).where(Message.user_id == user.id, Message.role == "user").order_by(Message.created_at.desc()).limit(limit)).all()
        return [m.content or "" for m in rows]

    def build_candidate(self, db: Session, user: User, intent: str) -> ProactiveCandidate:
        if intent == "specific_reply_followup":
            since = datetime.utcnow() - timedelta(hours=48)
            source = db.scalar(
                select(Message)
                .where(Message.user_id == user.id, Message.role == "user", Message.telegram_message_id.is_not(None), Message.created_at >= since)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            if source and any(k in (source.content or "") for k in ("بازار", "بازارچه", "رانندگی", "مسیر", "رسید", "جلسه", "سفارش", "کار")):
                text = "اون بازارچه چطور شد؟" if "بازار" in (source.content or "") else ("رسیدی؟" if any(k in (source.content or "") for k in ("رانندگی", "مسیر")) else "اون کاری که گفتی به کجا رسید؟")
                return ProactiveCandidate(text=text, kind="specific_reply_followup", source_message_id=source.telegram_message_id, source_message_text=source.content, reply_to_telegram_message_id=source.telegram_message_id, confidence=0.75)
        kind = "light_presence" if intent == "light_presence" else "simple_checkin"
        return ProactiveCandidate(text=random.choice(TEMPLATES[kind]), kind=kind, confidence=0.6)

    async def generate_proactive_text(self, db: Session, user: User, intent: str) -> ProactiveCandidate:
        logger.info("PROACTIVE_GENERATION_STARTED user_id=%s intent=%s", user.id, intent)
        prompt = """Generate one short casual Persian Telegram message.

Rules:
- It must sound like a normal person.
- No poetry.
- No literary/abstract language.
- No romance unless user clearly has that relationship and recent tone supports it.
- Be user-oriented: a simple check-in or concrete follow-up.
- Do not send bot self-status, inner-life reports, small digital events, or abstract mood fragments.
- Do not say you organized your thoughts, sorted small things, became calmer, or had a small inner change.
- Do not claim physical activities like listening to music, walking, sitting, seeing rain, etc.
- If referring to previous user content, the system will send it as a reply to that exact message. Otherwise do not refer to previous content.
- Max 90 characters.
- Prefer everyday phrasing.
Return only the message."""
        candidate = self.build_candidate(db, user, intent)
        if candidate.kind != "specific_reply_followup":
            try:
                result = await LLMClient().complete_result([{"role":"system","content":prompt}], model="qwen-3-6-plus", parameters={"temperature":0.55,"top_p":0.9,"max_tokens":80}, timeout=7)
                generated = (result.text or "").strip().strip('"“”')
                recent_texts = [m.text for m in self._recent_proactive(db, user, 12)]
                ok, reason = validate_proactive_text(generated, is_reply_followup=False, recent_texts=recent_texts)
                logger.info("PROACTIVE_CANDIDATE_GENERATED user_id=%s kind=%s chars=%s", user.id, candidate.kind, len(generated))
                if ok:
                    candidate.text = generated
                else:
                    logger.info("PROACTIVE_VALIDATION_FAILED user_id=%s reason=%s", user.id, reason)
            except Exception as exc:
                logger.info("PROACTIVE_GENERATION_FALLBACK user_id=%s reason=%s", user.id, type(exc).__name__)
        candidate.text = sanitize_output(candidate.text, user.id).text
        candidate.text, policy_issues = sanitize_user_facing_text(candidate.text, surface="proactive")
        if policy_issues:
            logger.info("OUTBOUND_TEXT_POLICY_APPLIED user_id=%s surface=proactive issues=%s", user.id, policy_issues)
        recent_texts = [m.text for m in self._recent_proactive(db, user, 12)]
        for _ in range(2):
            ok, reason = validate_proactive_text(candidate.text, is_reply_followup=bool(candidate.reply_to_telegram_message_id), recent_texts=recent_texts)
            if ok:
                logger.info("PROACTIVE_VARIANT_SELECTED user_id=%s kind=%s", user.id, candidate.kind)
                return candidate
            logger.info("PROACTIVE_VALIDATION_FAILED user_id=%s reason=%s", user.id, reason)
            replacement = choose_proactive_variant(candidate.kind, recent_texts)
            if not replacement:
                break
            candidate = ProactiveCandidate(text=replacement, kind="simple_checkin" if candidate.kind != "specific_reply_followup" else candidate.kind, confidence=0.55)
        return ProactiveCandidate(text="", kind="invalid", confidence=0.0)

    def choose_template(self, user: User) -> str:
        return random.choice(TEMPLATES[self.select_intent(user)])

    async def send_one(self, db: Session, user: User, svc: TelegramService | None = None, bypass_schedule: bool = False, force: bool = False) -> bool:
        now = datetime.utcnow()
        reason = None if force else self.skip_reason(db, user, now)
        if reason: return False
        if not bypass_schedule and user.next_proactive_at and user.next_proactive_at > now: return False
        recent_user_messages = self._recent_user_texts(db, user, 5)
        if not proactive_allowed_for_recent_user_messages(recent_user_messages):
            logger.info("PROACTIVE_COOLDOWN_USER_ANNOYED user_id=%s", user.id)
            user.next_proactive_at = now + timedelta(hours=12)
            db.flush()
            return False
        intent = self.select_intent(user)
        candidate = await self.generate_proactive_text(db, user, intent)
        recent_proactive_rows = self._recent_proactive(db, user, 12)
        recent_texts = [m.text for m in recent_proactive_rows]
        candidate.text, policy_issues = sanitize_user_facing_text(candidate.text, surface="proactive")
        if policy_issues:
            logger.info("OUTBOUND_TEXT_POLICY_APPLIED user_id=%s surface=proactive issues=%s", user.id, policy_issues)
        if policy_issues and not candidate.text:
            logger.info("PROACTIVE_SKIPPED user_id=%s reason=outbound_policy", user.id)
            return False
        if not candidate.text or not should_send_proactive(candidate, recent_texts=recent_texts):
            ok, reason = validate_proactive_text(candidate.text, is_reply_followup=bool(candidate.reply_to_telegram_message_id), recent_texts=recent_texts)
            reason = reason or "no_valid_variant"
            if references_context(candidate.text):
                logger.info("PROACTIVE_CONTEXT_REFERENCE_BLOCKED user_id=%s reason=%s", user.id, reason)
            logger.info("PROACTIVE_SKIPPED user_id=%s reason=%s", user.id, reason)
            return False
        row = ProactiveMessage(user_id=user.id, text=candidate.text, status="selected", created_at=now, intent=candidate.kind, extra_metadata={"reply_to_telegram_message_id": candidate.reply_to_telegram_message_id, "source_message_text": candidate.source_message_text})
        db.add(row); db.flush()
        try:
            reply_to = candidate.reply_to_telegram_message_id
            if reply_to:
                logger.info("PROACTIVE_REPLY_FOLLOWUP user_id=%s reply_to=%s", user.id, reply_to)
            await (svc or TelegramService("chat")).send_text(user.telegram_id, candidate.text, reply_to_message_id=reply_to, allow_sending_without_reply=False if reply_to else None)
            row.status = "sent"; row.sent_at = now; user.last_proactive_message_at = now
            self.schedule_next_proactive(db, user, now, reason="after_send")
            logger.info("PROACTIVE_SENT user_id=%s kind=%s reply_to=%s", user.id, candidate.kind, reply_to)
            return True
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            row.status = "failed"; row.error = f"http_{status}"
            if status == 403:
                user.proactive_blocked = True
            elif status == 400:
                user.next_proactive_at = now + timedelta(days=1)
            logger.info("PROACTIVE_SKIPPED user_id=%s reason=telegram_unreachable status=%s", user.id, status)
            return False
