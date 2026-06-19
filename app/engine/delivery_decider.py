from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.engine.mood_state import ensure_mood_defaults
from sqlalchemy import select
from app.models.sticker import StickerItem

logger = logging.getLogger(__name__)

@dataclass
class DeliveryDecision:
    delivery_type: str
    voice_probability: float
    sticker_probability: float
    sticker_file_id: str | None = None
    reason: str = ""


def _minutes_since(value: datetime | None) -> float | None:
    if not value:
        return None
    return (datetime.utcnow() - value).total_seconds() / 60


def _wants_sticker(text: str) -> bool:
    lowered = (text or "").lower()
    return any(x in lowered for x in ("sticker", "استیکر", "برچسب"))

def _wants_voice(text: str) -> bool:
    lowered = (text or "").lower()
    return any(x in lowered for x in ("voice", "ویس", "صدا", "بفرست صوتی", "پیام صوتی"))


def _emotional(text: str) -> bool:
    return any(x in (text or "") for x in ("دلم", "گریه", "ناراحتم", "تنها", "عزیزم", "دوستت", "🥺", "❤️"))


def _technical(text: str) -> bool:
    lowered = (text or "").lower().strip()
    return lowered.startswith("/") or any(x in lowered for x in ("admin", "onboard", "پرداخت", "receipt"))


def _catalog() -> dict[str, list[str]]:
    raw = getattr(get_settings(), "sticker_catalog_json", "") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning("STICKER_RESULT selected=False mood=unknown file_id_present=False sent=False reason=invalid_catalog_json")
        return {}
    out: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for mood, items in data.items():
            if isinstance(items, str):
                out[mood] = [items]
            elif isinstance(items, list):
                out[mood] = [str(x) for x in items if x]
    return out


def _select_sticker(mood: str, db=None, user_state: Any | None = None, explicit: bool = False) -> str | None:
    contexts = [mood, {"slightly_upset": "upset", "affectionate": "love", "teasing": "playful"}.get(mood, mood), "warm"]
    if explicit:
        contexts = ["playful", "warm", "neutral"] + contexts
    if db is not None:
        try:
            rows = list(db.scalars(select(StickerItem).where(StickerItem.is_active == True)).all())
            if rows:
                gender = (getattr(user_state, "partner_gender", None) or "").lower()
                style = (getattr(user_state, "partner_personality_type", None) or "").lower()
                stages = ["STRANGER", "ACQUAINTANCE", "FRIEND", "CLOSE", "INTIMATE", "BONDED"]
                current_stage = (getattr(user_state, "relationship_stage", None) or getattr(getattr(user_state, "relationship_state", None), "stage", None) or "STRANGER")
                current_rank = stages.index(current_stage) if current_stage in stages else 0
                def compatible(item):
                    min_stage = item.relationship_stage_min or "STRANGER"
                    min_rank = stages.index(min_stage) if min_stage in stages else 0
                    gender_ok = not item.persona_gender or not gender or item.persona_gender.lower() in gender or gender in item.persona_gender.lower()
                    style_ok = not item.persona_style or not style or item.persona_style.lower() in style or style in item.persona_style.lower()
                    return current_rank >= min_rank and gender_ok and style_ok
                filtered = [i for i in rows if compatible(i)] or rows
                for ctx in contexts:
                    matches = [i for i in filtered if (i.usage_context or "").lower() == ctx]
                    if matches:
                        item = random.choices(matches, weights=[max(1, int(i.weight or 1)) for i in matches], k=1)[0]
                        logger.info("STICKER_DB_SELECTED item_id=%s reason=context:%s", item.id, ctx)
                        return item.telegram_file_id
                item = random.choices(filtered, weights=[max(1, int(i.weight or 1)) for i in filtered], k=1)[0]
                logger.info("STICKER_DB_SELECTED item_id=%s reason=any_active", item.id)
                return item.telegram_file_id
        except Exception:
            logger.exception("STICKER_DB_SELECT_FAILED")
    catalog = _catalog()
    choices = catalog.get(mood) or catalog.get({"slightly_upset": "upset"}.get(mood, mood)) or catalog.get("warm") or []
    return random.choice(choices) if choices else None


def decide_delivery(user_state: Any, text: str, ai_response: str, db=None) -> DeliveryDecision:
    ensure_mood_defaults(user_state)
    reasons: list[str] = []
    mood = user_state.current_mood or "warm"
    voice_p = 0.08
    sticker_p = 0.10
    explicit_sticker = _wants_sticker(text)
    if _wants_voice(text):
        voice_p = max(voice_p, 0.70); reasons.append("user_asked_voice")
    if mood == "affectionate":
        voice_p += 0.10; sticker_p += 0.10
    if mood in {"tired"} or any(x in (text or "") for x in ("شب", "خواب", "خسته")):
        voice_p += 0.10
    if _emotional(text):
        voice_p += 0.10
    if len(ai_response or "") <= 220:
        voice_p += 0.10
    if mood in {"playful", "teasing"}:
        sticker_p += 0.12
    if _minutes_since(getattr(user_state, "last_voice_at", None)) is not None and _minutes_since(user_state.last_voice_at) < 8:
        voice_p = 0; reasons.append("voice_cooldown")
    if int(getattr(user_state, "consecutive_voice_count", 0) or 0) > 0:
        voice_p = 0; reasons.append("consecutive_voice")
    if len(ai_response or "") > 260 or _technical(text):
        voice_p = 0; reasons.append("voice_ineligible")
    recent_sticker = _minutes_since(getattr(user_state, "last_sticker_at", None))
    if explicit_sticker:
        sticker_p = 1.0; reasons.append("user_asked_sticker")
        if recent_sticker is not None and recent_sticker < 1:
            sticker_p = 0; reasons.append("sticker_antispam_cooldown")
    elif (recent_sticker is not None and recent_sticker < 5) or int(getattr(user_state, "consecutive_text_count", 0) or 0) < 4:
        sticker_p = 0; reasons.append("sticker_cooldown")
    sticker_file_id = _select_sticker(mood, db, user_state, explicit_sticker) if sticker_p > 0 else None
    if sticker_p > 0 and not sticker_file_id:
        sticker_p = 0; reasons.append("no_sticker_configured")
    r = random.random()
    if explicit_sticker and sticker_file_id and sticker_p > 0:
        dtype = "sticker_only"
    elif voice_p > 0 and r < voice_p:
        dtype = "voice"
    elif sticker_p > 0 and r < voice_p + 0.02:
        dtype = "sticker_only"
    elif sticker_p > 0 and r < voice_p + sticker_p:
        dtype = "text_plus_sticker"
    else:
        dtype = "text"
    if dtype in {"text_plus_sticker", "sticker_only"} and not sticker_file_id:
        dtype = "text"
    decision = DeliveryDecision(dtype, round(voice_p, 3), round(sticker_p, 3), sticker_file_id, ",".join(reasons) or "probability")
    logger.info("DELIVERY_DECISION type=%s voice_probability=%s sticker_probability=%s reason=%s", decision.delivery_type, decision.voice_probability, decision.sticker_probability, decision.reason)
    return decision


def mark_delivery(user_state: Any, delivery_type: str, sticker_sent: bool = False, voice_sent: bool = False) -> None:
    now = datetime.utcnow()
    user_state.last_delivery_type = delivery_type
    if voice_sent:
        user_state.last_voice_at = now
        user_state.consecutive_voice_count = int(user_state.consecutive_voice_count or 0) + 1
        user_state.consecutive_text_count = 0
    else:
        user_state.consecutive_voice_count = 0
        if delivery_type in {"text", "text_plus_sticker"}:
            user_state.consecutive_text_count = int(user_state.consecutive_text_count or 0) + 1
    if sticker_sent:
        user_state.last_sticker_at = now
        user_state.consecutive_sticker_count = int(user_state.consecutive_sticker_count or 0) + 1
    else:
        user_state.consecutive_sticker_count = 0
