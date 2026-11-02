"""Provider-specific speech accounting with refundable reservations."""
from app.core.config import get_settings
from app.models.usage import AiUsageEvent
from app.services.coin_pricing_service import CoinPricingService
from app.services.usage_billing_service import UsageBillingService
from app.services.provider_pricing_registry import REGISTRY_VERSION


def reserve_google_speech(db, user, text: str, key_suffix: str):
    settings = get_settings()
    quote = CoinPricingService().quote_tokens(db, provider="google", model=settings.google_tts_model,
        feature="tts_tokens", input_tokens=len(text.encode()) + 256,
        output_tokens=max(250, min(3000, settings.tts_max_audio_seconds * 25)))
    key = f"tts:{user.id}:{key_suffix}"
    charge = UsageBillingService().reserve(db, user=user, idempotency_key=key, feature="tts",
        provider="google", model=settings.google_tts_model, quote=quote, correlation_id=key)
    return charge, quote


def settle_google_speech(db, charge, audio):
    values = (getattr(audio, "input_tokens", None), getattr(audio, "output_tokens", None))
    known = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values)
    quote = CoinPricingService().quote_tokens(db, provider="google", model=charge.model,
        feature="tts_tokens", input_tokens=values[0], output_tokens=values[1]) if known else None
    event = AiUsageEvent(user_id=charge.user_id, provider="google", model=charge.model, feature="tts",
        input_tokens=values[0] if known else 0, output_tokens=values[1] if known else 0,
        total_tokens=sum(values) if known else 0, status="success", charged_coins=0,
        cost_usd=quote.provider_cost_usd if quote else None,
        cost_toman=quote.provider_cost_toman if quote else None,
        pricing_registry_version=REGISTRY_VERSION, correlation_id=charge.correlation_id,
        metadata_json={"usage_known": known, "cost_known": known})
    db.add(event); db.flush()
    if quote:
        UsageBillingService().settle(db, charge=charge, actual_quote=quote, usage_event=event)
    else:
        UsageBillingService().refund(db, charge=charge, error="google_speech_usage_unknown")
    return event
