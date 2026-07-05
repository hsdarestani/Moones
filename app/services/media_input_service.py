from __future__ import annotations
from datetime import date, datetime
import os, uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.media import MediaMessage
from app.models.settings import AppSetting
from app.models.user import User
from app.services.subscription_service import PAID_PLANS, SubscriptionService

FREE_PHOTO_MESSAGE = "دیدن عکس فقط برای پلن‌های فعال مونس بازه.\n\nبرای فعال‌کردنش برو ربات مدیریت:\n@moonesaibot"
FREE_VOICE_MESSAGE = "شنیدن وویس فقط برای پلن‌های فعال مونس بازه.\n\nبرای فعال‌کردنش برو ربات مدیریت:\n@moonesaibot"
PHOTO_QUOTA_MESSAGE = "سهمیه دیدن عکس این ماهت تموم شده.\n\nبرای ارتقا یا افزودن موجودی برو ربات مدیریت:\n@moonesaibot"
VOICE_QUOTA_MESSAGE = "سهمیه وویس این ماهت تموم شده.\n\nبرای ارتقا یا افزودن موجودی برو ربات مدیریت:\n@moonesaibot"
DEFAULT_QUOTAS = {"basic_monthly_image_inputs":30,"premium_monthly_image_inputs":150,"vip_monthly_image_inputs":500,"plus_monthly_image_inputs":150,"monthly_monthly_image_inputs":150,"mini_monthly_image_inputs":30,"basic_monthly_voice_inputs":60,"premium_monthly_voice_inputs":300,"vip_monthly_voice_inputs":1000,"plus_monthly_voice_inputs":300,"monthly_monthly_voice_inputs":300,"mini_monthly_voice_inputs":60}

def _enabled(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1","true","yes","on"}

def generate_media_ref(kind: str, db_id: int | None = None) -> str:
    prefix = {"photo":"PH","document_image":"PH","voice":"VO","audio":"VO"}.get(kind, kind[:2].upper())
    suffix = f"{int(db_id):06d}" if db_id else uuid.uuid4().hex[:8].upper()
    return f"MNS-{prefix}-{date.today().strftime('%Y%m%d')}-{suffix}"

def setting_int(db: Session, key: str, default: int) -> int:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row:
        row = AppSetting(key=key, value=str(default), value_type="integer", description="Monthly paid media input quota")
        db.add(row); db.flush()
        return default
    try: return int(row.value)
    except Exception: return default

class MediaInputService:
    def __init__(self) -> None:
        self.subscriptions = SubscriptionService()

    def plan_name(self, db: Session, user: User) -> str:
        return self.subscriptions.active_plan_code(db, user)

    def can_use_media(self, db: Session, user: User, kind: str) -> tuple[bool, str | None]:
        settings = get_settings(); plan = self.plan_name(db, user)
        if kind == "photo" and not settings.image_input_enabled: return False, FREE_PHOTO_MESSAGE
        if kind in {"voice","audio"} and not settings.voice_input_enabled: return False, FREE_VOICE_MESSAGE
        if plan not in PAID_PLANS and not settings.free_plan_media_enabled:
            return False, FREE_PHOTO_MESSAGE if kind == "photo" else FREE_VOICE_MESSAGE
        if plan not in PAID_PLANS:
            return True, None
        usage = self.subscriptions.get_or_create_today_usage(db, user)
        quota_key = f"{plan}_monthly_{'image' if kind == 'photo' else 'voice'}_inputs"
        default_key = quota_key if quota_key in DEFAULT_QUOTAS else f"basic_monthly_{'image' if kind == 'photo' else 'voice'}_inputs"
        quota = setting_int(db, quota_key, DEFAULT_QUOTAS.get(default_key, 30))
        used = int(getattr(usage, f"monthly_{'image' if kind == 'photo' else 'voice'}_inputs_used", 0) or 0)
        if used >= quota:
            return False, PHOTO_QUOTA_MESSAGE if kind == "photo" else VOICE_QUOTA_MESSAGE
        return True, None

    def record_media_usage(self, db: Session, user: User, kind: str) -> None:
        usage = self.subscriptions.get_or_create_today_usage(db, user)
        field = f"monthly_{'image' if kind == 'photo' else 'voice'}_inputs_used"
        setattr(usage, field, int(getattr(usage, field, 0) or 0) + 1)

    def create_media(self, db: Session, user: User, *, kind: str, message_id: int | None = None, telegram_message_id: int | None = None, telegram_chat_id: int | None = None, telegram_file_unique_id: str | None = None, telegram_file_id: str | None = None, **kw) -> MediaMessage:
        media = MediaMessage(media_ref=f"tmp-{uuid.uuid4()}", user_id=user.id, message_id=message_id, kind=kind, telegram_message_id=telegram_message_id, telegram_chat_id=telegram_chat_id, telegram_file_unique_id=telegram_file_unique_id, telegram_file_id=telegram_file_id if get_settings().store_telegram_file_id else None, stored_path=None, **kw)
        db.add(media); db.flush(); media.media_ref = generate_media_ref(kind, media.id); db.flush(); return media
# Static acceptance markers: monthly_image_inputs_used, monthly_voice_inputs_used
