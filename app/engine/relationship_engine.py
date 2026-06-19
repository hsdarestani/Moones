from datetime import datetime, timedelta, timezone

from app.engine.emotion_engine import Emotion
from app.models.relationship import Relationship, RelationshipStage, normalize_relationship_stage, relationship_stage_rank


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def ensure_relationship(user_id: int, existing: Relationship | None) -> Relationship:
    state = existing or Relationship(user_id=user_id)
    state.stage = normalize_relationship_stage(state.stage)
    return state


def update_state(state: Relationship, message_count: int, emotion: Emotion, last_seen_at: datetime | None) -> Relationship:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    previous_stage = normalize_relationship_stage(state.stage)
    state.stage = previous_stage
    returned_recently = last_seen_at is not None and now - last_seen_at < timedelta(days=2)
    emotional_depth = 0.02 if emotion in {Emotion.LONELY, Emotion.STRESSED, Emotion.EXCITED} else 0.01

    state.intimacy = clamp((state.intimacy or 0.05) + emotional_depth)
    state.attachment = clamp((state.attachment or 0.05) + (0.025 if returned_recently else 0.01))
    state.trust = clamp((state.trust or 0.05) + min(0.03, message_count * 0.002 + 0.01))
    state.attraction = clamp((state.attraction or 0.03) + (0.015 if state.intimacy > 0.2 else 0.005))
    state.dependency = clamp((state.dependency or 0.0) + 0.003)
    state.volatility = clamp((state.volatility if state.volatility is not None else 0.2) - (0.015 if returned_recently else 0.005))
    derived_stage = _derive_stage(state).value
    if relationship_stage_rank(derived_stage) >= relationship_stage_rank(previous_stage):
        state.stage = derived_stage
    else:
        state.stage = previous_stage
    state.updated_at = now
    return state


def _derive_stage(state: Relationship) -> RelationshipStage:
    score = (state.intimacy + state.attachment + state.trust + state.attraction) / 4
    if score >= 0.82 and state.trust >= 0.65:
        return RelationshipStage.LOVER
    if score >= 0.58 and state.trust >= 0.45:
        return RelationshipStage.PARTNER
    if score >= 0.32:
        return RelationshipStage.CLOSE
    if score >= 0.15:
        return RelationshipStage.WARM
    return RelationshipStage.STRANGER


def update_simple_chat_relationship(state: Relationship, user_message: str, assistant_response: str = "", current_mood: str | None = None) -> Relationship:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    def val(v: float | None, default: float = 0.0) -> float:
        try:
            return clamp(default if v is None else float(v))
        except Exception:
            return clamp(default)

    state.intimacy = val(state.intimacy)
    state.attachment = val(state.attachment)
    state.trust = val(state.trust)
    state.attraction = val(state.attraction)
    state.dependency = val(state.dependency)
    state.volatility = val(state.volatility, 0.0)
    previous_stage = normalize_relationship_stage(state.stage)
    state.stage = previous_stage

    text = (user_message or "").lower()
    positive_terms = ("دوستت دارم", "عزیزم", "قربونت", "مرسی", "ممنون", "خوشحالم", "دلم برات", "عشق", "مهربون", "ناز")
    rude_terms = ("خفه", "احمق", "برو گمشو", "متنفرم", "مزخرف", "بدرد", "بیخود", "لعنتی")
    affectionate = any(term in text for term in positive_terms) or current_mood in {"affectionate", "playful"}
    rude = any(term in text for term in rude_terms) or current_mood in {"slightly_upset", "cold"}

    intimacy_delta = 0.006
    trust_delta = 0.005
    attachment_delta = 0.004
    attraction_delta = 0.003
    volatility_delta = -0.002
    if affectionate:
        intimacy_delta += 0.018
        trust_delta += 0.014
        attachment_delta += 0.010
        attraction_delta += 0.012
        volatility_delta -= 0.006
    if rude:
        intimacy_delta = min(intimacy_delta, 0.002)
        trust_delta = -0.006
        attraction_delta = -0.004
        volatility_delta = 0.025

    state.intimacy = clamp(state.intimacy + intimacy_delta)
    state.trust = clamp(state.trust + trust_delta)
    state.attachment = clamp(state.attachment + attachment_delta)
    state.attraction = clamp(state.attraction + attraction_delta)
    state.dependency = clamp(state.dependency + 0.001)
    state.volatility = clamp(state.volatility + volatility_delta)
    derived_stage = _derive_stage(state).value
    if relationship_stage_rank(derived_stage) >= relationship_stage_rank(previous_stage):
        state.stage = derived_stage
    else:
        state.stage = previous_stage
    state.updated_at = now
    return state
