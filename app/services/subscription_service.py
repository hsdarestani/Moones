from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.subscription import DailyUsage, Subscription
from app.models.user import User
from app.services.plan_config import get_plan_configs
from app.services.settings_service import SettingsService

ACTIVE = "active"
FREE_LIMIT_MESSAGE = """امروز به سقف پیام‌های رایگان رسیدی 😅
برای ادامه گفتگو، می‌تونی کیف پولت رو شارژ کنی و اشتراک فعال کنی."""
PAID_LIMIT_MESSAGE = """امروز خیلی باهم حرف زدیم 😅
برای اینکه کیفیت جواب‌هام خوب بمونه، بهتره یه کم فاصله بدیم و بعداً ادامه بدیم.

فردا دوباره با انرژی برمی‌گردیم 💙"""
LIMIT_MESSAGE = PAID_LIMIT_MESSAGE


class SubscriptionService:
    def get_active_subscription(self, db: Session, user: User) -> Subscription | None:
        now = datetime.utcnow()
        sub = db.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id, Subscription.status == ACTIVE)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
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
        db.add(sub)
        db.flush()
        return sub

    def activate_plan(self, db: Session, user: User, plan: str) -> Subscription:
        configs = get_plan_configs()
        if plan not in {"daily", "weekly", "monthly", "free"}:
            raise ValueError("Invalid subscription plan")
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE)).all():
            sub.status = "cancelled"
        db.flush()
        config = configs[plan]
        starts_at = datetime.utcnow()
        sub = Subscription(user_id=user.id, plan=plan, status=ACTIVE, starts_at=starts_at, expires_at=starts_at + config.duration if config.duration else None)
        sub.user = user
        db.add(sub)
        db.flush()
        return sub

    def cancel(self, db: Session, user: User) -> None:
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE)).all():
            sub.status = "cancelled"
        db.flush()
        self.ensure_free_subscription(db, user)

    def get_or_create_today_usage(self, db: Session, user: User) -> DailyUsage:
        today = date.today()
        usage = db.scalar(select(DailyUsage).where(DailyUsage.user_id == user.id, DailyUsage.date == today))
        if usage:
            return usage
        usage = DailyUsage(user_id=user.id, date=today)
        usage.user = user
        db.add(usage)
        db.flush()
        return usage

    def daily_limit(self, db: Session, user: User) -> int:
        sub = self.get_active_subscription(db, user)
        plan = sub.plan if sub else "free"
        return SettingsService().get_int(db, f"limits.{plan}.daily_messages", get_plan_configs().get(plan, get_plan_configs()["free"]).daily_message_limit)

    def can_send_message(self, db: Session, user: User) -> tuple[bool, int, DailyUsage]:
        usage = self.get_or_create_today_usage(db, user)
        limit = self.daily_limit(db, user)
        return usage.messages_used < limit, limit, usage

    def record_successful_llm_response(self, db: Session, user: User, input_tokens: int | None = None, output_tokens: int | None = None) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user)
        usage.messages_used += 1
        usage.llm_requests += 1
        if input_tokens is not None:
            usage.input_tokens = (usage.input_tokens or 0) + input_tokens
        if output_tokens is not None:
            usage.output_tokens = (usage.output_tokens or 0) + output_tokens
        return usage

    def reset_today_usage(self, db: Session, user: User) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user)
        usage.messages_used = 0
        usage.llm_requests = 0
        usage.input_tokens = 0
        usage.output_tokens = 0
        usage.daily_stickers_sent = 0
        return usage
