from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.addon import AddonProduct, AddonUpsellEvent, UserAddon

logger = logging.getLogger(__name__)

GLOBAL_COOLDOWN_HOURS = 12
DEFAULT_ADDON_COOLDOWN_HOURS = 24
DEFAULT_MAX_SUGGESTIONS_PER_7D = 2

HARD_FORBIDDEN_KEYWORDS = (
    "زیر ۱۸", "زیر18", "زیر سن", "زیرسن", "بچه", "کودک", "نوجوان", "نابالغ", "minor", "underage",
    "اجبار", "مجبور", "زور", "زورکی", "تجاوز", "بی رضایت", "بی‌رضایت", "بدون رضایت",
    "خشونت", "کتک", "آسیب", "خودکشی", "خودکشی کنم", "خودآزاری", "بکشمت", "می کشمت", "می‌کشمت",
)
UNDERAGE_VALUES = {"زیر ۱۸", "زیر18", "under18", "under_18", "minor", "underage"}


@dataclass(frozen=True)
class AddonUpsellSuggestion:
    addon_key: str
    title: str
    text: str
    cta_text: str
    management_deeplink: str
    score: float
    reason: str
    product_id: int | None = None

    def message_text(self, management_username: str = "") -> str:
        return (
            "یه چیزی هست که دقیقاً برای همین موقع‌ها ساخته شده:\n\n"
            f"{self.title}\n\n"
            f"{self.text}\n\n"
            "برای فعال‌کردنش برو ربات مدیریت مونس:\n"
            f"{management_username}"
        )

    def keyboard(self) -> dict:
        return {"inline_keyboard": [[{"text": self.cta_text, "url": self.management_deeplink}]]}


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u200c", " ")).strip().lower()


def _metadata(product: AddonProduct) -> dict[str, Any]:
    return product.metadata_json if isinstance(product.metadata_json, dict) else {}


def _is_underage(user: Any) -> bool:
    return _normalize(getattr(user, "partner_age_range", "")) in {_normalize(v) for v in UNDERAGE_VALUES}


def _contains_any(text: str, needles: list[str] | tuple[str, ...]) -> str | None:
    for needle in needles:
        n = _normalize(needle)
        if n and n in text:
            return needle
    return None


def _score_for(meta: dict[str, Any], combined_text: str) -> tuple[float, str]:
    triggers = [str(x) for x in (meta.get("trigger_keywords") or []) if str(x).strip()]
    if not triggers:
        return 0.0, "no_trigger_keywords"
    matched = []
    for kw in triggers:
        if _normalize(kw) in combined_text:
            matched.append(kw)
    if not matched:
        return 0.0, "no_trigger_match"
    score = min(1.0, 0.45 + (0.25 * len(matched)))
    return score, "trigger_keywords:" + ",".join(matched[:5])


def _suppressed(user_id: int, addon_key: str, reason: str) -> None:
    logger.info("ADDON_UPSELL_SUPPRESSED user_id=%s addon_key=%s reason=%s", user_id, addon_key, reason)


def record_addon_upsell_event(
    db: Session,
    *,
    user_id: int,
    addon_key: str,
    event_type: str,
    reason: str | None = None,
    score: float | None = None,
    message_id: int | None = None,
    metadata_json: dict | None = None,
) -> AddonUpsellEvent:
    event = AddonUpsellEvent(
        user_id=user_id,
        addon_key=addon_key,
        event_type=event_type,
        reason=reason,
        score=score,
        message_id=message_id,
        metadata_json=metadata_json,
    )
    db.add(event)
    db.flush()
    if event_type == "sent":
        logger.info("ADDON_UPSELL_SENT user_id=%s addon_key=%s", user_id, addon_key)
    return event


def _sent_count(db: Session, user_id: int, addon_key: str | None, since: datetime) -> int:
    stmt = select(func.count(AddonUpsellEvent.id)).where(
        AddonUpsellEvent.user_id == user_id,
        AddonUpsellEvent.event_type == "sent",
        AddonUpsellEvent.created_at >= since,
    )
    if addon_key:
        stmt = stmt.where(AddonUpsellEvent.addon_key == addon_key)
    return int(db.scalar(stmt) or 0)


def detect_addon_opportunity(
    db: Session,
    *,
    user,
    user_text: str,
    assistant_text: str | None = None,
    recent_user_texts: list[str] | None = None,
):
    user_id = int(getattr(user, "id", 0) or 0)
    now = datetime.utcnow()
    recent_blob = " ".join(recent_user_texts or [])
    combined_text = _normalize(f"{recent_blob} {user_text or ''} {assistant_text or ''}")
    if not user_id or not combined_text:
        return None

    forbidden = _contains_any(combined_text, HARD_FORBIDDEN_KEYWORDS)
    products = list(db.scalars(select(AddonProduct).where(AddonProduct.is_active == True).order_by(AddonProduct.sort_order, AddonProduct.id)).all())
    if not products:
        return None

    if _sent_count(db, user_id, None, now - timedelta(hours=GLOBAL_COOLDOWN_HOURS)) > 0:
        for product in products:
            if _metadata(product).get("upsell_enabled") is True:
                _suppressed(user_id, product.key, "global_cooldown")
        return None

    best: AddonUpsellSuggestion | None = None
    for product in products:
        meta = _metadata(product)
        if meta.get("upsell_enabled") is not True:
            continue
        addon_key = product.key
        if forbidden:
            _suppressed(user_id, addon_key, f"hard_forbidden:{forbidden}")
            continue
        negative = _contains_any(combined_text, [str(x) for x in (meta.get("negative_keywords") or [])])
        if negative:
            _suppressed(user_id, addon_key, f"negative_keyword:{negative}")
            continue
        owns = db.scalar(select(UserAddon.id).where(UserAddon.user_id == user_id, UserAddon.addon_key == addon_key, UserAddon.status == "active"))
        if owns:
            _suppressed(user_id, addon_key, "already_owned")
            continue
        if bool(meta.get("requires_adult")) and _is_underage(user):
            _suppressed(user_id, addon_key, "underage")
            continue
        cooldown_hours = int(meta.get("cooldown_hours") or DEFAULT_ADDON_COOLDOWN_HOURS)
        if _sent_count(db, user_id, addon_key, now - timedelta(hours=cooldown_hours)) > 0:
            _suppressed(user_id, addon_key, "addon_cooldown")
            continue
        max_7d = int(meta.get("max_suggestions_per_7d") or DEFAULT_MAX_SUGGESTIONS_PER_7D)
        if _sent_count(db, user_id, addon_key, now - timedelta(days=7)) >= max_7d:
            _suppressed(user_id, addon_key, "max_suggestions_per_7d")
            continue
        score, reason = _score_for(meta, combined_text)
        min_score = float(meta.get("min_score") or 0.6)
        if score < min_score:
            _suppressed(user_id, addon_key, f"score_below_min:{score:.2f}")
            continue
        suggestion = AddonUpsellSuggestion(
            addon_key=addon_key,
            title=str(meta.get("upsell_title") or product.title),
            text=str(meta.get("upsell_text") or product.description or ""),
            cta_text=str(meta.get("cta_text") or "فعال‌کردن افزودنی"),
            management_deeplink=str(meta.get("management_deeplink")),
            score=score,
            reason=reason,
            product_id=product.id,
        )
        if best is None or suggestion.score > best.score:
            best = suggestion
    if best:
        logger.info("ADDON_UPSELL_DETECTED user_id=%s addon_key=%s score=%.2f reason=%s", user_id, best.addon_key, best.score, best.reason)
    return best
