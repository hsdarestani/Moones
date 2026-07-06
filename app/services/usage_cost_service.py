from __future__ import annotations

import logging
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.usage import AiUsageEvent
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)
settings = SettingsService()


def _f(db: Session, key: str, default: float = 0.0) -> float:
    return settings.get_float(db, key, default)


def _exchange(db: Session) -> float:
    return _f(db, "billing.usd_to_toman", 60000.0)


def _pricing_key(provider: str, model: str, suffix: str) -> str:
    return f"pricing.{provider}.{model}.{suffix}"


def estimate_llm_cost(*, model: str, input_tokens: int, output_tokens: int, db: Session) -> dict:
    threshold = settings.get_int(db, "pricing.venice.long_context_threshold_tokens", 256000)
    long_ctx = (int(input_tokens or 0) + int(output_tokens or 0)) >= threshold
    in_key = "long_context_input_per_1m_usd" if long_ctx else "input_per_1m_usd"
    out_key = "long_context_output_per_1m_usd" if long_ctx else "output_per_1m_usd"
    unit_in = _f(db, _pricing_key("venice", model, in_key), _f(db, _pricing_key("venice", model, "input_per_1m_usd"), 0.0))
    unit_out = _f(db, _pricing_key("venice", model, out_key), _f(db, _pricing_key("venice", model, "output_per_1m_usd"), 0.0))
    cost = (int(input_tokens or 0) / 1_000_000.0 * unit_in) + (int(output_tokens or 0) / 1_000_000.0 * unit_out)
    return {"unit_input_usd": unit_in, "unit_output_usd": unit_out, "cost_usd": cost, "cost_toman": cost * _exchange(db), "pricing_missing": unit_in == 0 and unit_out == 0}


def estimate_audio_cost(*, model: str, audio_seconds: float, db: Session) -> dict:
    unit = _f(db, _pricing_key("venice", model, "per_audio_second_usd"), 0.0)
    cost = float(audio_seconds or 0) * unit
    return {"unit_audio_second_usd": unit, "cost_usd": cost, "cost_toman": cost * _exchange(db), "pricing_missing": unit == 0}


def estimate_tts_cost(*, model: str, character_count: int = 0, audio_seconds: float = 0, db: Session) -> dict:
    unit_char = _f(db, _pricing_key("venice", model, "per_character_usd"), 0.0)
    unit_audio = _f(db, _pricing_key("venice", model, "per_audio_second_usd"), 0.0)
    cost = int(character_count or 0) * unit_char + float(audio_seconds or 0) * unit_audio
    return {"unit_character_usd": unit_char, "unit_audio_second_usd": unit_audio, "cost_usd": cost, "cost_toman": cost * _exchange(db), "pricing_missing": unit_char == 0 and unit_audio == 0}


def record_ai_usage_event(db: Session, *, user_id: int | None, feature: str, model: str, message_id: int | None = None, media_message_id: int | None = None, provider: str = "venice", plan: str | None = None, input_tokens: int = 0, output_tokens: int = 0, audio_seconds: float = 0, image_count: int = 0, character_count: int = 0, status: str = "success", error: str | None = None, metadata_json: dict | None = None) -> AiUsageEvent:
    input_tokens = int(input_tokens or 0); output_tokens = int(output_tokens or 0)
    pricing = estimate_audio_cost(model=model, audio_seconds=audio_seconds, db=db) if feature == "stt" else estimate_tts_cost(model=model, character_count=character_count, audio_seconds=audio_seconds, db=db) if feature == "tts" else estimate_llm_cost(model=model, input_tokens=input_tokens, output_tokens=output_tokens, db=db)
    event = AiUsageEvent(user_id=user_id, message_id=message_id, media_message_id=media_message_id, provider=provider, feature=feature, model=model, plan=plan, input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens, audio_seconds=audio_seconds or 0, image_count=image_count or 0, character_count=character_count or 0, unit_input_usd=pricing.get("unit_input_usd", 0), unit_output_usd=pricing.get("unit_output_usd", 0), unit_audio_second_usd=pricing.get("unit_audio_second_usd", 0), unit_image_usd=pricing.get("unit_image_usd", 0), unit_character_usd=pricing.get("unit_character_usd", 0), cost_usd=Decimal(str(pricing.get("cost_usd", 0))), cost_toman=Decimal(str(pricing.get("cost_toman", 0))), status=status, error=error, metadata_json=metadata_json)
    db.add(event); db.flush()
    if pricing.get("pricing_missing") and (input_tokens or output_tokens or audio_seconds or image_count or character_count):
        logger.warning("AI_USAGE_PRICING_MISSING model=%s feature=%s", model, feature)
    logger.info("AI_USAGE_RECORDED user_id=%s feature=%s model=%s input=%s output=%s cost_usd=%s", user_id, feature, model, input_tokens, output_tokens, event.cost_usd)
    return event
