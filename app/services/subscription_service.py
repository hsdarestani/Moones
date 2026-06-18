from datetime import date, datetime
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.subscription import DailyUsage, Subscription
from app.models.user import User
from app.services.plan_config import get_plan_configs

logger = logging.getLogger(__name__)
ACTIVE = "active"
LIMIT_MESSAGE = "به محدودیت استفاده امروز رسیدی 😅\nبرای ادامه گفتگو، سطح پلنت رو ارتقا بده یا فردا دوباره برگرد 💙"


class SubscriptionService:
    def get_active_subscription(self, db: Session, user: User) -> Subscription | None:
        now = datetime.utcnow()
        sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE).order_by(Subscription.created_at.desc()).limit(1))
        if sub and sub.expires_at and sub.expires_at <= now:
            sub.status = "expired"
            db.flush()
            return None
        return sub

    def ensure_free_subscription(self, db: Session, user: User) -> Subscription:
        active = self.get_active_subscription(db, user)
        if active:
            return active
        sub = Subscription(user_id=user.id, plan="free", status=ACTIVE, starts_at=datetime.utcnow(), expires_at=None)
        sub.user = user
        db.add(sub); db.flush()
        return sub

    def active_plan_code(self, db: Session, user: User) -> str:
        sub = self.get_active_subscription(db, user)
        return sub.plan if sub else "free"

    def plan_config(self, db: Session, user: User):
        configs = get_plan_configs()
        return configs.get(self.active_plan_code(db, user), configs["free"])

    def activate_plan(self, db: Session, user: User, plan: str) -> Subscription:
        configs = get_plan_configs()
        if plan not in configs:
            raise ValueError("Invalid subscription plan")
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE)).all():
            sub.status = "cancelled"
        db.flush()
        config = configs[plan]
        starts_at = datetime.utcnow()
        sub = Subscription(user_id=user.id, plan=plan, status=ACTIVE, starts_at=starts_at, expires_at=starts_at + config.duration if config.duration else None)
        sub.user = user
        db.add(sub); db.flush()
        logger.info("ADMIN_ACTIONS action=activate_plan user_id=%s plan=%s", user.id, plan)
        return sub

    def cancel(self, db: Session, user: User) -> None:
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE)).all():
            sub.status = "cancelled"
        db.flush(); self.ensure_free_subscription(db, user)
        logger.info("ADMIN_ACTIONS action=cancel_plan user_id=%s", user.id)

    def get_or_create_today_usage(self, db: Session, user: User) -> DailyUsage:
        today = date.today()
        usage = db.scalar(select(DailyUsage).where(DailyUsage.user_id == user.id, DailyUsage.date == today))
        if usage: return usage
        usage = DailyUsage(user_id=user.id, date=today); usage.user = user
        db.add(usage); db.flush(); return usage

    def daily_token_limit(self, db: Session, user: User) -> int:
        return self.plan_config(db, user).daily_token_limit

    def total_tokens_used(self, usage: DailyUsage) -> int:
        return int(usage.input_tokens or 0) + int(usage.output_tokens or 0) + int(getattr(usage, "voice_tokens", 0) or 0)

    def can_generate(self, db: Session, user: User) -> tuple[bool, int, DailyUsage]:
        usage = self.get_or_create_today_usage(db, user)
        limit = self.daily_token_limit(db, user)
        return self.total_tokens_used(usage) < limit, limit, usage

    def can_send_voice(self, db: Session, user: User) -> tuple[bool, int, DailyUsage]:
        usage = self.get_or_create_today_usage(db, user); limit = self.plan_config(db, user).daily_voice_limit
        return int(getattr(usage, "daily_voice_sent", 0) or 0) < limit, limit, usage

    def can_send_sticker(self, db: Session, user: User) -> tuple[bool, int, DailyUsage]:
        usage = self.get_or_create_today_usage(db, user); limit = self.plan_config(db, user).daily_sticker_limit
        return int(usage.daily_stickers_sent or 0) < limit, limit, usage

    def record_successful_llm_response(self, db: Session, user: User, input_tokens: int | None = None, output_tokens: int | None = None) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user)
        usage.llm_requests += 1
        usage.messages_used += 1  # legacy analytics only
        usage.input_tokens = (usage.input_tokens or 0) + int(input_tokens or 0)
        usage.output_tokens = (usage.output_tokens or 0) + int(output_tokens or 0)
        logger.info("TOKEN_USAGE user_id=%s input=%s output=%s voice=%s total=%s", user.id, input_tokens or 0, output_tokens or 0, getattr(usage, "voice_tokens", 0) or 0, self.total_tokens_used(usage))
        return usage

    def record_voice(self, db: Session, user: User, text: str) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user)
        usage.daily_voice_sent = int(getattr(usage, "daily_voice_sent", 0) or 0) + 1
        usage.voice_tokens = int(getattr(usage, "voice_tokens", 0) or 0) + max(1, len(text or "") // 4)
        logger.info("TOKEN_USAGE user_id=%s voice_tokens=%s total=%s", user.id, usage.voice_tokens, self.total_tokens_used(usage))
        return usage

    def record_sticker(self, db: Session, user: User) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user); usage.daily_stickers_sent += 1; return usage

    def reset_today_usage(self, db: Session, user: User) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user)
        usage.messages_used = usage.llm_requests = usage.input_tokens = usage.output_tokens = usage.voice_tokens = usage.daily_voice_sent = usage.daily_stickers_sent = 0
        logger.info("ADMIN_ACTIONS action=reset_usage user_id=%s", user.id)
        return usage
