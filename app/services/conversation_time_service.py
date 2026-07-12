from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message
from app.models.settings import AppSetting

logger = logging.getLogger(__name__)
DEFAULT_TIMEZONE = "Asia/Tehran"

@dataclass(frozen=True)
class ConversationTimeContext:
    utc_now: datetime
    local_now: datetime
    timezone_name: str
    local_date: date
    local_weekday: str
    local_hour: int
    daypart: str
    previous_user_message_at: datetime | None
    previous_assistant_message_at: datetime | None
    seconds_since_previous_user: int | None
    seconds_since_previous_assistant: int | None
    gap_bucket: str
    crossed_local_midnight: bool
    is_first_conversation: bool
    is_active_session: bool
    recent_turn_count: int
    session_turn_count: int
    session_started_at: datetime

PERSIAN_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]

def as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def daypart_for_hour(hour: int) -> str:
    if 4 <= hour < 7:
        return "dawn"
    if 7 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "noon"
    if 14 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 22:
        return "evening"
    if 22 <= hour or hour < 1:
        return "night"
    return "late_night"

class ConversationTimeService:
    def __init__(self, now_provider=None) -> None:
        self.now_provider = now_provider

    def utcnow(self) -> datetime:
        if self.now_provider:
            value = self.now_provider()
            return as_aware_utc(value) or datetime.now(timezone.utc)
        return datetime.now(timezone.utc)

    def resolve_timezone(self, db: Session, user) -> tuple[str, ZoneInfo]:
        candidates = [getattr(user, "timezone_name", None)]
        try:
            row = db.scalar(select(AppSetting.value).where(AppSetting.key == "roleplay.default_timezone"))
            candidates.append(row)
        except Exception:
            candidates.append(None)
        candidates.append(DEFAULT_TIMEZONE)
        for value in candidates:
            name = (value or "").strip()
            if not name:
                continue
            try:
                return name, ZoneInfo(name)
            except ZoneInfoNotFoundError:
                logger.warning("TIMEZONE_FALLBACK user_id=%s invalid_timezone=%s", getattr(user, "id", None), name)
        return DEFAULT_TIMEZONE, ZoneInfo(DEFAULT_TIMEZONE)

    def build_context(self, db: Session, user, *, utc_now: datetime | None = None, exclude_message_id: int | None = None) -> ConversationTimeContext:
        utc_now = as_aware_utc(utc_now) or self.utcnow()
        timezone_name, tz = self.resolve_timezone(db, user)
        local_now = utc_now.astimezone(tz)
        if not getattr(user, "timezone_name", None):
            try:
                user.timezone_name = timezone_name
                user.timezone_source = getattr(user, "timezone_source", None) or "default"
            except Exception:
                pass
        q = select(Message).where(Message.user_id == user.id, Message.role.in_(["user", "assistant"]))
        if exclude_message_id is not None:
            q = q.where(Message.id != exclude_message_id)
        history = list(reversed(db.scalars(q.order_by(Message.created_at.desc(), Message.id.desc()).limit(80)).all()))
        prev_user = next((m for m in reversed(history) if m.role == "user"), None)
        prev_assistant = next((m for m in reversed(history) if m.role == "assistant"), None)
        prev_user_at = as_aware_utc(getattr(prev_user, "created_at", None)) or as_aware_utc(getattr(user, "last_user_message_at", None))
        prev_assistant_at = as_aware_utc(getattr(prev_assistant, "created_at", None)) or as_aware_utc(getattr(user, "last_assistant_message_at", None))
        sec_user = int((utc_now - prev_user_at).total_seconds()) if prev_user_at else None
        sec_assistant = int((utc_now - prev_assistant_at).total_seconds()) if prev_assistant_at else None
        crossed = bool(prev_user_at and prev_user_at.astimezone(tz).date() != local_now.date())
        bucket = self.classify_gap(sec_user, crossed)
        recent_turns = sum(1 for m in history if (utc_now - (as_aware_utc(m.created_at) or utc_now)) <= timedelta(minutes=30))
        session_started = utc_now
        session_turns = 0
        last_at = None
        for m in reversed(history):
            m_at = as_aware_utc(m.created_at) or utc_now
            if last_at and (last_at - m_at) >= timedelta(minutes=30):
                break
            session_started = m_at
            session_turns += 1
            last_at = m_at
        ctx = ConversationTimeContext(
            utc_now=utc_now, local_now=local_now, timezone_name=timezone_name, local_date=local_now.date(),
            local_weekday=PERSIAN_WEEKDAYS[local_now.weekday()], local_hour=local_now.hour, daypart=daypart_for_hour(local_now.hour),
            previous_user_message_at=prev_user_at, previous_assistant_message_at=prev_assistant_at,
            seconds_since_previous_user=sec_user, seconds_since_previous_assistant=sec_assistant,
            gap_bucket=bucket, crossed_local_midnight=crossed, is_first_conversation=prev_user_at is None,
            is_active_session=bucket in {"rapid_exchange", "active_session"}, recent_turn_count=recent_turns,
            session_turn_count=session_turns, session_started_at=session_started,
        )
        logger.info("CONVERSATION_GAP_CLASSIFIED user_id=%s timezone=%s local_hour=%s gap_bucket=%s", user.id, timezone_name, local_now.hour, bucket)
        logger.info("TIME_CONTEXT_BUILT user_id=%s timezone=%s local_hour=%s gap_bucket=%s", user.id, timezone_name, local_now.hour, bucket)
        return ctx

    @staticmethod
    def classify_gap(seconds: int | None, crossed_midnight: bool) -> str:
        if seconds is None:
            return "first_contact"
        if seconds < 120:
            return "rapid_exchange"
        if seconds < 600:
            return "active_session"
        if seconds < 10800:
            return "brief_pause"
        if crossed_midnight and seconds < 129600:
            return "overnight_return"
        if seconds < 43200:
            return "same_day_return"
        if seconds < 129600:
            return "day_return"
        if seconds < 604800:
            return "days_away"
        return "long_return"
