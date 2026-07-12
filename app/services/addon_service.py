import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.engine.relationship_engine import ensure_relationship
from app.models.addon import AddonProduct, UserAddon
from app.models.relationship import RelationshipStage
from app.models.user import User
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)
INTIMACY_MAX_UNLOCK = "intimacy_max_unlock"
IMAGE_GENERATION_UNLOCK = "image_generation_unlock"
MAX_INTIMACY_LEVEL = 100

INTIMACY_UPSELL_METADATA = {
    "upsell_enabled": True,
    "requires_adult": True,
    "trigger_keywords": ["سکسچت", "چت بزرگسال", "بزرگسال", "شیطون‌تر", "شیطون تر", "صمیمی‌تر", "صمیمی تر", "نزدیک‌تر شو", "نزدیک تر شو", "هنوز زوده", "بذار بیشتر آشنا شیم", "چرا نمیذاری", "چرا نمی‌ذاری"],
    "negative_keywords": ["زیر ۱۸", "زیر18", "بچه", "نوجوان", "اجبار", "زور", "تجاوز", "بی‌رضایت", "بی رضایت"],
    "min_score": 0.6,
    "cooldown_hours": 24,
    "max_suggestions_per_7d": 2,
    "upsell_title": "🔥 افزایش صمیمیت رابطه",
    "upsell_text": "اگه می‌خوای رابطه‌تون سریع‌تر از حالت آشنایی رد بشه و صمیمی‌تر بشه، این افزودنی سطح صمیمیت مونس رو به بالاترین درجه می‌رسونه.",
    "cta_text": "فعال‌کردن افزایش صمیمیت",
    
}

class AddonService:
    def list_active_addons(self, db: Session) -> list[AddonProduct]:
        seed_default_addon(db)
        return list(db.scalars(select(AddonProduct).where(AddonProduct.is_active == True).order_by(AddonProduct.sort_order, AddonProduct.id)).all())
    def get_addon_price_coins(self, db: Session, addon_key: str) -> int:
        product = db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key))
        if addon_key == INTIMACY_MAX_UNLOCK:
            return int(product.price_coins or ((product.price_toman + 99)//100) if product else 1000)
        return int(product.price_coins or ((product.price_toman + 99)//100) if product else 0)
    def user_has_addon(self, db: Session, user_id: int, addon_key: str) -> bool:
        addon = db.scalar(select(UserAddon).where(UserAddon.user_id == user_id, UserAddon.addon_key == addon_key, UserAddon.status == "active"))
        if not addon:
            return False
        if addon.expires_at is None:
            return True
        now = datetime.utcnow()
        if addon.expires_at > now:
            return True
        addon.status = "expired"
        addon.updated_at = now
        db.flush()
        logger.info("ADDON_EXPIRED user_id=%s addon_key=%s expires_at=%s", user_id, addon_key, addon.expires_at)
        return False
    def activate_addon_for_user(self, db: Session, *, user_id: int, addon_key: str, payment_receipt_id: int | None = None, source: str = "manual_payment", price_paid_toman: int | None = None, price_paid_coins: int | None = None) -> UserAddon:
        addon = db.scalar(select(UserAddon).where(UserAddon.user_id == user_id, UserAddon.addon_key == addon_key))
        if not addon:
            addon = UserAddon(user_id=user_id, addon_key=addon_key)
            db.add(addon)
        if addon_key == INTIMACY_MAX_UNLOCK and self._is_underage(db, user_id):
            addon.status = "revoked"; addon.source = source; addon.payment_receipt_id = payment_receipt_id; addon.price_paid_toman = price_paid_toman; addon.price_paid_coins = price_paid_coins; addon.updated_at = datetime.utcnow()
            logger.warning("ADDON_INTIMACY_MAX_BLOCKED_UNDER18 user_id=%s", user_id)
            db.flush(); return addon
        product = db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key))
        metadata = product.metadata_json if product and isinstance(product.metadata_json, dict) else {}
        duration_days = metadata.get("duration_days")
        now = datetime.utcnow()
        addon.status = "active"; addon.source = source; addon.payment_receipt_id = payment_receipt_id; addon.price_paid_toman = price_paid_toman; addon.price_paid_coins = price_paid_coins; addon.activated_at = now; addon.updated_at = now
        if isinstance(duration_days, int) and duration_days > 0:
            base = addon.expires_at if addon.expires_at and addon.expires_at > now else now
            addon.expires_at = base + timedelta(days=duration_days)
        else:
            addon.expires_at = None
        if addon_key == INTIMACY_MAX_UNLOCK:
            self.apply_intimacy_max_unlock(db, user_id)
            logger.info("ADDON_INTIMACY_MAX_UNLOCKED user_id=%s source=%s", user_id, source)
        db.flush(); return addon
    def _is_underage(self, db: Session, user_id: int) -> bool:
        user = db.get(User, user_id)
        return str(getattr(user, "partner_age_range", "") or "").lower() in {"زیر ۱۸", "زیر18", "under18", "under_18", "minor"}
    def apply_intimacy_max_unlock(self, db: Session, user_id: int) -> None:
        user = db.get(User, user_id)
        if not user: return
        user.intimacy_override_max = True; user.mature_intimacy_unlocked = True; user.mature_intimacy_unlocked_at = datetime.utcnow(); user.intimacy_level = MAX_INTIMACY_LEVEL
        rel = ensure_relationship(user.id, user.relationship_state)
        if rel.id is None: db.add(rel); user.relationship_state = rel
        rel.intimacy = 1.0; rel.trust = max(rel.trust or 0, 1.0); rel.attachment = max(rel.attachment or 0, 1.0); rel.attraction = max(rel.attraction or 0, 1.0); rel.stage = RelationshipStage.LOVER.value

IMAGE_GENERATION_METADATA = {"duration_days": None, "management_deeplink": "", "copy_fa": "این افزودنی درخواست و دریافت عکس از مونس را فعال می‌کند؛ هزینه هر عکس جداگانه از کیف پول کم می‌شود."}

def seed_image_generation_addon(db: Session) -> AddonProduct:
    product = db.scalar(select(AddonProduct).where(AddonProduct.key == IMAGE_GENERATION_UNLOCK))
    if not product:
        product = AddonProduct(key=IMAGE_GENERATION_UNLOCK, title="دریافت عکس از مونس", description="امکان درخواست و دریافت عکس از مونس رو فعال می‌کنه. هزینه هر عکس جداگانه از کیف پول کم می‌شه.", price_toman=0, price_coins=500, is_active=True, sort_order=20, metadata_json=dict(IMAGE_GENERATION_METADATA))
        db.add(product); db.flush()
    else:
        current = product.metadata_json if isinstance(product.metadata_json, dict) else {}
        merged = dict(current); merged.update({k:v for k,v in IMAGE_GENERATION_METADATA.items() if k not in merged})
        product.metadata_json = merged
        if product.title in {"ساخت تصویر مونس", "تولید تصویر مونس"}: product.title = "دریافت عکس از مونس"
        if product.description in {"باز کردن درخواست تصویر از مونس؛ هر تصویر هزینه مصرف جداگانه دارد.", "باز کردن ساخت تصویر از مونس؛ هر تصویر هزینه مصرف جداگانه دارد."}: product.description = "امکان درخواست و دریافت عکس از مونس رو فعال می‌کنه. هزینه هر عکس جداگانه از کیف پول کم می‌شه."
        if not product.price_coins: product.price_coins = 500
        db.flush()
    return product

def seed_default_addon(db: Session) -> AddonProduct:
    product = db.scalar(select(AddonProduct).where(AddonProduct.key == INTIMACY_MAX_UNLOCK))
    if not product:
        product = AddonProduct(key=INTIMACY_MAX_UNLOCK, title="افزایش صمیمیت رابطه", description="سطح صمیمیت رابطه‌ات با مونس رو به بالاترین حالت باز می‌کنه.", price_toman=100000, price_coins=1000, is_active=True, sort_order=10, metadata_json=dict(INTIMACY_UPSELL_METADATA))
        db.add(product); db.flush()
    else:
        current = product.metadata_json if isinstance(product.metadata_json, dict) else {}
        merged = dict(current)
        for key, value in INTIMACY_UPSELL_METADATA.items():
            merged.setdefault(key, value)
        product.metadata_json = merged
        if product.description in {"صمیمیت رابطه‌ات با مونس را به بالاترین سطح می‌رساند، بدون تغییر پلن.", "صمیمیت رابطه‌ات با مونس را به بالاترین سطح می‌رساند."}: product.description = "سطح صمیمیت رابطه‌ات با مونس رو به بالاترین حالت باز می‌کنه."
        db.flush()
    seed_image_generation_addon(db)
    return product

_service = AddonService()
def list_active_addons(db): return _service.list_active_addons(db)
def get_addon_price_toman(db, addon_key): return _service.get_addon_price_coins(db, addon_key)
def get_addon_price_coins(db, addon_key): return _service.get_addon_price_coins(db, addon_key)
def user_has_addon(db, user_id, addon_key): return _service.user_has_addon(db, user_id, addon_key)
def activate_addon_for_user(db, **kwargs): return _service.activate_addon_for_user(db, **kwargs)
def apply_intimacy_max_unlock(db, user_id): return _service.apply_intimacy_max_unlock(db, user_id)

def seed_image_addon(db): return seed_image_generation_addon(db)
