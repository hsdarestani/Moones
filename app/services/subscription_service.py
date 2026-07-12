from datetime import date, datetime
import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.subscription import DailyUsage, Subscription
from app.models.user import User
from dataclasses import replace
from app.services.plan_config import get_plan_configs
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)
ACTIVE = "active"
LIMIT_MESSAGE = "امروز ظرفیت گفت‌وگوت پر شده 😅\nبرای ادامه، می‌تونی تجربه کامل‌تر رو فعال کنی یا فردا دوباره برگردی 🌙"

PLAN_ORDER = {"free": 0, "daily": 0, "trial": 0, "mini": 1, "basic": 2, "plus": 3, "vip": 4, "monthly": 3, "premium": 4}
PAID_PLANS = {"mini", "basic", "plus", "vip", "monthly", "premium"}

def round_toman(value: float) -> int:
    return max(0, int(round(float(value or 0) / 1000.0) * 1000))


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

    def plan_config_by_code(self, db: Session, code: str):
        configs = get_plan_configs(); cfg = configs.get(code, configs["free"])
        price = SettingsService().get_int(db, f"subscription.{code}.price_coins", cfg.price_coins)
        return replace(cfg, price_coins=price)

    def plan_config(self, db: Session, user: User):
        return self.plan_config_by_code(db, self.active_plan_code(db, user))

    def activate_plan(self, db: Session, user: User, plan: str) -> Subscription:
        configs = get_plan_configs()
        if plan not in configs:
            raise ValueError("Invalid subscription plan")
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE)).all():
            sub.status = "cancelled"
        db.flush()
        config = self.plan_config_by_code(db, plan)
        starts_at = datetime.utcnow()
        sub = Subscription(user_id=user.id, plan=plan, status=ACTIVE, starts_at=starts_at, expires_at=starts_at + config.duration if config.duration else None)
        sub.user = user
        db.add(sub); db.flush()
        logger.info("ADMIN_ACTIONS action=activate_plan user_id=%s plan=%s", user.id, plan)
        return sub


    def quote_upgrade(self, db: Session, user: User, target_plan: str, now: datetime | None = None) -> dict:
        now = now or datetime.utcnow()
        configs = get_plan_configs()
        if target_plan not in configs:
            raise ValueError("Invalid subscription plan")
        current = self.get_active_subscription(db, user)
        current_plan = current.plan if current else "free"
        target_price = self.plan_config_by_code(db, target_plan).price_coins
        current_price = self.plan_config_by_code(db, current_plan).price_coins
        if current_plan in PAID_PLANS and target_plan == current_plan:
            config = self.plan_config_by_code(db, target_plan)
            base = current.expires_at if current and current.expires_at and current.expires_at > now else now
            new_expires_at = base + config.duration if config.duration else None
            return {"renewal": True, "upgrade": False, "current_plan": current_plan, "target_plan": target_plan, "amount": target_price, "expires_at": current.expires_at if current else None, "new_expires_at": new_expires_at, "metadata": {"payment_type": "subscription_renewal", "plan": target_plan, "renewal_amount": target_price, "current_expires_at": current.expires_at.isoformat() if current and current.expires_at else None, "new_expires_at": new_expires_at.isoformat() if new_expires_at else None}}
        if PLAN_ORDER.get(target_plan, 0) < PLAN_ORDER.get(current_plan, 0) and current_plan in PAID_PLANS:
            return {"upgrade": False, "renewal": False, "reason": "lower_plan", "current_plan": current_plan, "target_plan": target_plan, "amount": 0}
        if current_plan not in PAID_PLANS or not current or not current.expires_at:
            amount = target_price if current_plan not in PAID_PLANS else max(0, target_price - current_price)
            logger.info("SUBSCRIPTION_UPGRADE_QUOTE user_id=%s from=%s to=%s amount=%s remaining_seconds=%s", user.id, current_plan, target_plan, amount, None)
            return {"upgrade": current_plan in PAID_PLANS, "current_plan": current_plan, "target_plan": target_plan, "amount": amount, "expires_at": None, "remaining_seconds": None, "metadata": {"payment_type": "plan_upgrade" if current_plan in PAID_PLANS else "subscription_activation", "current_plan": current_plan, "target_plan": target_plan, "prorated_amount": amount, "remaining_seconds": None}}
        remaining = max(0, int((current.expires_at - now).total_seconds()))
        total = int(((current.expires_at - current.starts_at).total_seconds()) if current.starts_at and current.expires_at else 0)
        if total <= 0:
            amount = max(0, target_price - current_price)
            logger.warning("SUBSCRIPTION_UPGRADE_QUOTE_FALLBACK user_id=%s reason=missing_period from=%s to=%s", user.id, current_plan, target_plan)
        else:
            amount = round_toman(max(0, (target_price-current_price) * remaining / total))
        logger.info("SUBSCRIPTION_UPGRADE_QUOTE user_id=%s from=%s to=%s amount=%s remaining_seconds=%s", user.id, current_plan, target_plan, amount, remaining)
        return {"upgrade": True, "current_plan": current_plan, "target_plan": target_plan, "amount": amount, "expires_at": current.expires_at, "remaining_seconds": remaining, "metadata": {"payment_type":"plan_upgrade", "current_plan":current_plan, "target_plan":target_plan, "prorated_amount":amount, "previous_expires_at":current.expires_at.isoformat(), "new_expires_at":current.expires_at.isoformat(), "remaining_seconds":remaining}}

    def renew_plan(self, db: Session, user: User, plan: str, now: datetime | None = None) -> Subscription:
        now = now or datetime.utcnow()
        configs = get_plan_configs()
        if plan not in configs or plan not in PAID_PLANS:
            raise ValueError("Invalid paid subscription plan")
        current = self.get_active_subscription(db, user)
        if not current or current.plan != plan or current.plan not in PAID_PLANS:
            raise ValueError("No active matching paid subscription to renew")
        old_expires_at = current.expires_at
        base = old_expires_at if old_expires_at and old_expires_at > now else now
        cfg = self.plan_config_by_code(db, plan)
        current.expires_at = base + cfg.duration if cfg.duration else None
        current.status = ACTIVE
        db.flush()
        logger.info("SUBSCRIPTION_RENEWED user_id=%s plan=%s old_expires_at=%s new_expires_at=%s", user.id, plan, old_expires_at, current.expires_at)
        return current

    def apply_prorated_upgrade(self, db: Session, user: User, target_plan: str, previous_expires_at: datetime) -> Subscription:
        current_plan = self.active_plan_code(db, user)
        for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.status == ACTIVE)).all():
            sub.status = "cancelled"
        starts_at = datetime.utcnow()
        sub = Subscription(user_id=user.id, plan=target_plan, status=ACTIVE, starts_at=starts_at, expires_at=previous_expires_at)
        sub.user = user; db.add(sub); db.flush()
        logger.info("SUBSCRIPTION_UPGRADE_APPLIED user_id=%s from=%s to=%s expires_at=%s", user.id, current_plan, target_plan, previous_expires_at)
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


    def can_use_media_input(self, db: Session, user: User, kind: str) -> tuple[bool, str | None]:
        from app.services.media_input_service import MediaInputService
        return MediaInputService().can_use_media(db, user, kind)

    def record_media_input(self, db: Session, user: User, kind: str) -> None:
        from app.services.media_input_service import MediaInputService
        MediaInputService().record_media_usage(db, user, kind)

    def reset_today_usage(self, db: Session, user: User) -> DailyUsage:
        usage = self.get_or_create_today_usage(db, user)
        usage.messages_used = usage.llm_requests = usage.input_tokens = usage.output_tokens = usage.voice_tokens = usage.daily_voice_sent = usage.daily_stickers_sent = usage.monthly_image_inputs_used = usage.monthly_voice_inputs_used = 0
        logger.info("ADMIN_ACTIONS action=reset_usage user_id=%s", user.id)
        return usage
