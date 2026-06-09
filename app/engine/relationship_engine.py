from datetime import datetime, timedelta, timezone

from app.engine.emotion_engine import Emotion
from app.models.relationship import Relationship, RelationshipStage


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def ensure_relationship(user_id: int, existing: Relationship | None) -> Relationship:
    return existing or Relationship(user_id=user_id)


def update_state(state: Relationship, message_count: int, emotion: Emotion, last_seen_at: datetime | None) -> Relationship:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    returned_recently = last_seen_at is not None and now - last_seen_at < timedelta(days=2)
    emotional_depth = 0.02 if emotion in {Emotion.LONELY, Emotion.STRESSED, Emotion.EXCITED} else 0.01

    state.intimacy = clamp((state.intimacy or 0.05) + emotional_depth)
    state.attachment = clamp((state.attachment or 0.05) + (0.025 if returned_recently else 0.01))
    state.trust = clamp((state.trust or 0.05) + min(0.03, message_count * 0.002 + 0.01))
    state.attraction = clamp((state.attraction or 0.03) + (0.015 if state.intimacy > 0.2 else 0.005))
    state.dependency = clamp((state.dependency or 0.0) + 0.003)
    state.volatility = clamp((state.volatility if state.volatility is not None else 0.2) - (0.015 if returned_recently else 0.005))
    state.stage = _derive_stage(state).value
    state.updated_at = now
    return state


def _derive_stage(state: Relationship) -> RelationshipStage:
    score = (state.intimacy + state.attachment + state.trust + state.attraction) / 4
    if score >= 0.7 and state.trust >= 0.55:
        return RelationshipStage.PARTNER
    if score >= 0.5:
        return RelationshipStage.ROMANTIC
    if score >= 0.3:
        return RelationshipStage.FRIEND
    if score >= 0.15:
        return RelationshipStage.FAMILIAR
    return RelationshipStage.STRANGER
