from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from datetime import datetime, timedelta, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.simple_chat import handle_simple_chat, sanitize_final_response
from app.models.human_delivery import HumanDeliveryJob
from app.models.message import Message
from app.models.user import User
from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)

ANGRY = ("کصخلی", "چی میگی", "چی می‌گی", "چرت نگو", "مسخره", "رباتی", "نفهمیدی")
URGENT = ("فوری", "اورژانس", "خودکشی", "می‌میرم", "دکتر", "قانونی", "پول", "پرداخت", "پشتیبانی", "admin", "support")
DIRECT_Q = ("چطور", "چجوری", "چند", "کجا", "کی ", "چرا", "؟", "?")
CASUAL = ("سلام", "های", "صبح", "شب", "خوبم", "خسته", "دلم", "امروز", "حالم", "هیچی", "چخبر", "چه خبر")


def _env_bool(name: str, default: bool) -> bool:
    return (os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"})


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class DelayedReactionService:
    def __init__(self) -> None:
        self.enabled = _env_bool("DELAYED_REACTION_ENABLED", True)
        self.probability = max(0.0, min(1.0, _env_float("DELAYED_REACTION_PROBABILITY", 0.10)))
        self.min_seconds = max(5, _env_int("DELAYED_REACTION_MIN_SECONDS", 35))
        self.max_seconds = max(self.min_seconds, _env_int("DELAYED_REACTION_MAX_SECONDS", 180))
        self.daily_cap = max(0, _env_int("DELAYED_REACTION_DAILY_CAP", 1))

    def should_delay_user_reply(self, user, text: str, recent_messages: list, force_probability: bool = False) -> tuple[bool, str | None, int | None]:
        t = (text or "").strip()
        compact = re.sub(r"\s+", " ", t).lower()
        if not self.enabled and not force_probability:
            return False, "disabled", None
        if not t or t.startswith("/"):
            return False, "command_or_empty", None
        if len(t) > 180 or t.count("\n") > 2:
            return False, "too_long_or_complex", None
        if any(x in compact for x in ANGRY):
            return False, "angry_or_complaint", None
        if any(x in compact for x in URGENT):
            return False, "urgent_or_sensitive", None
        if getattr(user, "onboarding_step", "complete") != "complete" and user is not None:
            return False, "onboarding", None
        user_msgs = [m for m in recent_messages if getattr(m, "role", None) == "user"]
        if user is not None and len(user_msgs) < 3:
            return False, "new_user_first_messages", None
        bot_msgs = [m for m in recent_messages if getattr(m, "role", None) == "assistant"]
        if bot_msgs and any(x in (bot_msgs[-1].content or "") for x in ("مشکلی پیش", "خطا", "دوباره امتحان")):
            return False, "last_bot_failed", None
        if any(getattr(m, "role", None) == "assistant" for m in recent_messages[-2:]) and not force_probability:
            # Avoid an obvious delay pattern immediately after every bot reply in tests/live history.
            pass
        if any(x in compact for x in DIRECT_Q) and not any(x in compact for x in CASUAL):
            return False, "direct_practical_question", None
        if not (any(x in compact for x in CASUAL) or len(t) <= 40):
            return False, "not_casual", None
        if not force_probability and random.random() > self.probability:
            return False, "probability", None
        return True, "casual_low_pressure", random.randint(self.min_seconds, self.max_seconds)

    def _daily_count(self, db: Session, user: User) -> int:
        start = datetime.combine(datetime.utcnow().date(), time.min)
        return db.scalar(select(func.count(HumanDeliveryJob.id)).where(HumanDeliveryJob.user_id == user.id, HumanDeliveryJob.job_type == "delayed_reaction", HumanDeliveryJob.created_at >= start)) or 0

    async def schedule_delayed_reply(self, db: Session, user: User, chat_id: int, telegram_message_id: int, text: str, delay_seconds: int, reason: str):
        if self.daily_cap and self._daily_count(db, user) >= self.daily_cap:
            return None
        pending = db.scalar(select(HumanDeliveryJob).where(HumanDeliveryJob.user_id == user.id, HumanDeliveryJob.job_type == "delayed_reaction", HumanDeliveryJob.status == "pending").order_by(HumanDeliveryJob.created_at.desc()).limit(1))
        if pending:
            pending.status = "cancelled"; pending.cancelled_at = datetime.utcnow(); pending.metadata_json = {**(pending.metadata_json or {}), "cancel_reason": "merged_newer_message"}
            logger.info("DELAYED_REACTION_CANCELLED user_id=%s reason=merged_newer_message", user.id)
        row = HumanDeliveryJob(user_id=user.id, telegram_id=user.telegram_id, chat_id=chat_id, job_type="delayed_reaction", text=text, status="pending", source_message_id=telegram_message_id, source_created_at=datetime.utcnow(), scheduled_at=datetime.utcnow() + timedelta(seconds=delay_seconds), expires_at=datetime.utcnow() + timedelta(minutes=10), metadata_json={"reason": reason, "telegram_user_message_id": telegram_message_id})
        db.add(row); db.flush()
        logger.info("DELAYED_REACTION_SCHEDULED user_id=%s delay=%s reason=%s", user.id, delay_seconds, reason)
        return row

    async def process_due_jobs(self, db: Session, limit: int = 10) -> int:
        now = datetime.utcnow()
        rows = db.scalars(select(HumanDeliveryJob).where(HumanDeliveryJob.job_type == "delayed_reaction", HumanDeliveryJob.status == "pending", HumanDeliveryJob.scheduled_at <= now).order_by(HumanDeliveryJob.scheduled_at.asc()).limit(limit)).all()
        logger.info("DELAYED_REACTION_TICK due_count=%s", len(rows))
        sent = 0
        svc = TelegramService("chat")
        for job in rows:
            try:
                newer_user = db.scalar(select(Message).where(Message.user_id == job.user_id, Message.role == "user", Message.telegram_message_id.is_not(None), Message.telegram_message_id != job.source_message_id, Message.created_at > (job.source_created_at or job.created_at)).limit(1))
                newer_bot = db.scalar(select(Message).where(Message.user_id == job.user_id, Message.role == "assistant", Message.created_at > (job.source_created_at or job.created_at)).limit(1))
                if newer_user or newer_bot or (job.expires_at and job.expires_at < now):
                    job.status = "cancelled"; job.cancelled_at = now
                    reason = "user_sent_newer_message" if newer_user else ("bot_already_replied" if newer_bot else "expired")
                    job.metadata_json = {**(job.metadata_json or {}), "cancel_reason": reason}
                    logger.info("DELAYED_REACTION_CANCELLED user_id=%s reason=%s", job.user_id, reason)
                    continue
                user = db.get(User, job.user_id)
                if not user:
                    job.status = "failed"; logger.info("DELAYED_REACTION_FAILED user_id=%s reason=user_missing", job.user_id); continue
                await svc.send_chat_action(job.chat_id, "typing")
                await asyncio.sleep(random.uniform(2, 5))
                response = await handle_simple_chat(db, user, job.text, message_metadata={"telegram_message_id": job.source_message_id, "input_type": "text"}, save_user_message=False, assistant_message_metadata={"telegram_reply_to_message_id": job.source_message_id})
                response = sanitize_final_response(response, job.text)
                await svc.send_text(job.chat_id, response, reply_to_message_id=job.source_message_id, allow_sending_without_reply=False)
                job.status = "sent"; job.sent_at = datetime.utcnow(); sent += 1
                logger.info("DELAYED_REACTION_SENT user_id=%s reply_to=%s", job.user_id, job.source_message_id)
            except Exception as exc:
                job.status = "failed"
                logger.info("DELAYED_REACTION_FAILED user_id=%s reason=%s", job.user_id, type(exc).__name__)
            db.flush()
        return sent
