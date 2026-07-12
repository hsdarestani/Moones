from __future__ import annotations
import logging
from decimal import Decimal
from sqlalchemy.orm import Session
from app.models.usage import AiUsageEvent
from app.services.coin_pricing_service import CoinPricingService
from app.services.provider_pricing_registry import REGISTRY_VERSION
logger=logging.getLogger(__name__)
pricing_service=CoinPricingService()

def estimate_llm_cost(*, model: str, input_tokens: int, output_tokens: int, db: Session) -> dict:
    try: q=pricing_service.quote_tokens(db, model=model, feature="chat", input_tokens=input_tokens, output_tokens=output_tokens, long_context=(int(input_tokens or 0)+int(output_tokens or 0)>=256000))
    except Exception: q=pricing_service.quote_usd(db, Decimal("0"), {"registry_version":REGISTRY_VERSION})
    snap=q.pricing_snapshot; return {"unit_input_usd": Decimal(str((snap.get('input') or {}).get('standard_rate_usd') or 0)), "unit_output_usd": Decimal(str((snap.get('output') or {}).get('standard_rate_usd') or 0)), "cost_usd": q.provider_cost_usd, "cost_toman": q.provider_cost_toman, "charged_coins": q.charged_coins, "pricing_missing": q.provider_cost_usd == 0 and (input_tokens or output_tokens)}

def estimate_audio_cost(*, model: str, audio_seconds: float, db: Session) -> dict:
    try: q=pricing_service.quote_unit(db, model=model, feature="stt", quantity=audio_seconds)
    except Exception: q=pricing_service.quote_usd(db, Decimal("0"), {"registry_version":REGISTRY_VERSION})
    return {"unit_audio_second_usd": Decimal(str((q.pricing_snapshot.get('price') or {}).get('standard_rate_usd') or 0)), "cost_usd": q.provider_cost_usd, "cost_toman": q.provider_cost_toman, "charged_coins": q.charged_coins, "pricing_missing": q.provider_cost_usd == 0 and audio_seconds}

def estimate_tts_cost(*, model: str, character_count: int = 0, audio_seconds: float = 0, db: Session) -> dict:
    try: q=pricing_service.quote_unit(db, model=model, feature="tts", quantity=character_count)
    except Exception: q=pricing_service.quote_usd(db, Decimal("0"), {"registry_version":REGISTRY_VERSION})
    return {"unit_character_usd": Decimal(str((q.pricing_snapshot.get('price') or {}).get('standard_rate_usd') or 0)), "unit_audio_second_usd": 0, "cost_usd": q.provider_cost_usd, "cost_toman": q.provider_cost_toman, "charged_coins": q.charged_coins, "pricing_missing": q.provider_cost_usd == 0 and character_count}

def record_ai_usage_event(db: Session, *, user_id: int | None, feature: str, model: str, message_id: int | None = None, media_message_id: int | None = None, provider: str = "venice", plan: str | None = None, input_tokens: int = 0, output_tokens: int = 0, audio_seconds: float = 0, image_count: int = 0, character_count: int = 0, status: str = "success", error: str | None = None, metadata_json: dict | None = None) -> AiUsageEvent:
    input_tokens=int(input_tokens or 0); output_tokens=int(output_tokens or 0)
    pricing=estimate_audio_cost(model=model,audio_seconds=audio_seconds,db=db) if feature=="stt" else estimate_tts_cost(model=model,character_count=character_count,audio_seconds=audio_seconds,db=db) if feature=="tts" else estimate_llm_cost(model=model,input_tokens=input_tokens,output_tokens=output_tokens,db=db)
    event=AiUsageEvent(user_id=user_id,message_id=message_id,media_message_id=media_message_id,provider=provider,feature=feature,model=model,plan=None,input_tokens=input_tokens,output_tokens=output_tokens,total_tokens=input_tokens+output_tokens,audio_seconds=audio_seconds or 0,image_count=image_count or 0,character_count=character_count or 0,unit_input_usd=pricing.get("unit_input_usd",0),unit_output_usd=pricing.get("unit_output_usd",0),unit_audio_second_usd=pricing.get("unit_audio_second_usd",0),unit_image_usd=pricing.get("unit_image_usd",0),unit_character_usd=pricing.get("unit_character_usd",0),cost_usd=Decimal(str(pricing.get("cost_usd",0))),cost_toman=Decimal(str(pricing.get("cost_toman",0))),charged_coins=int(pricing.get("charged_coins",0) or 0),pricing_registry_version=REGISTRY_VERSION,status=status,error=error,metadata_json=metadata_json)
    db.add(event); db.flush(); return event
