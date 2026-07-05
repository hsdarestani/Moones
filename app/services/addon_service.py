import logging
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.engine.relationship_engine import ensure_relationship
from app.models.addon import AddonProduct, UserAddon
from app.models.relationship import RelationshipStage
from app.models.user import User
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)
INTIMACY_MAX_UNLOCK = "intimacy_max_unlock"
MAX_INTIMACY_LEVEL = 100

class AddonService:
    def list_active_addons(self, db: Session) -> list[AddonProduct]:
        seed_default_addon(db)
        return list(db.scalars(select(AddonProduct).where(AddonProduct.is_active == True).order_by(AddonProduct.sort_order, AddonProduct.id)).all())
    def get_addon_price_toman(self, db: Session, addon_key: str) -> int:
        product = db.scalar(select(AddonProduct).where(AddonProduct.key == addon_key))
        if addon_key == INTIMACY_MAX_UNLOCK:
            return SettingsService().get_int(db, "addon_intimacy_max_price_toman", product.price_toman if product else 100000)
        return int(product.price_toman if product else 0)
    def user_has_addon(self, db: Session, user_id: int, addon_key: str) -> bool:
        return bool(db.scalar(select(UserAddon).where(UserAddon.user_id == user_id, UserAddon.addon_key == addon_key, UserAddon.status == "active")))
    def activate_addon_for_user(self, db: Session, *, user_id: int, addon_key: str, payment_receipt_id: int | None = None, source: str = "manual_payment", price_paid_toman: int | None = None) -> UserAddon:
        addon = db.scalar(select(UserAddon).where(UserAddon.user_id == user_id, UserAddon.addon_key == addon_key))
        if not addon:
            addon = UserAddon(user_id=user_id, addon_key=addon_key)
            db.add(addon)
        if addon_key == INTIMACY_MAX_UNLOCK and self._is_underage(db, user_id):
            addon.status = "revoked"; addon.source = source; addon.payment_receipt_id = payment_receipt_id; addon.price_paid_toman = price_paid_toman; addon.updated_at = datetime.utcnow()
            logger.warning("ADDON_INTIMACY_MAX_BLOCKED_UNDER18 user_id=%s", user_id)
            db.flush(); return addon
        addon.status = "active"; addon.source = source; addon.payment_receipt_id = payment_receipt_id; addon.price_paid_toman = price_paid_toman; addon.activated_at = datetime.utcnow(); addon.updated_at = datetime.utcnow()
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

def seed_default_addon(db: Session) -> AddonProduct:
    product = db.scalar(select(AddonProduct).where(AddonProduct.key == INTIMACY_MAX_UNLOCK))
    if not product:
        product = AddonProduct(key=INTIMACY_MAX_UNLOCK, title="افزایش صمیمیت رابطه", description="صمیمیت رابطه‌ات با مونس را به بالاترین سطح می‌رساند، بدون تغییر پلن.", price_toman=100000, is_active=True, sort_order=10)
        db.add(product); db.flush()
    return product

_service = AddonService()
def list_active_addons(db): return _service.list_active_addons(db)
def get_addon_price_toman(db, addon_key): return _service.get_addon_price_toman(db, addon_key)
def user_has_addon(db, user_id, addon_key): return _service.user_has_addon(db, user_id, addon_key)
def activate_addon_for_user(db, **kwargs): return _service.activate_addon_for_user(db, **kwargs)
def apply_intimacy_max_unlock(db, user_id): return _service.apply_intimacy_max_unlock(db, user_id)
