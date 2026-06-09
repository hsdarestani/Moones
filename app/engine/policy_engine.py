from dataclasses import dataclass

from app.engine.emotion_engine import Emotion
from app.models.relationship import Relationship, RelationshipStage


@dataclass(slots=True)
class ResponsePolicy:
    tone: str
    depth: float
    flirt_level: float
    memory_usage: float


def compute_policy(state: Relationship, emotion: Emotion) -> ResponsePolicy:
    tone_by_emotion = {
        Emotion.LONELY: "warm_reassuring",
        Emotion.STRESSED: "calm_brief",
        Emotion.HAPPY: "playful",
        Emotion.BORED: "novelty_injection",
        Emotion.EXCITED: "playful_energized",
        Emotion.NEUTRAL: "warm_neutral",
    }
    stage = RelationshipStage(state.stage)
    stage_flirt = {
        RelationshipStage.STRANGER: 0.05,
        RelationshipStage.FAMILIAR: 0.15,
        RelationshipStage.FRIEND: 0.25,
        RelationshipStage.ROMANTIC: 0.45,
        RelationshipStage.PARTNER: 0.6,
    }[stage]
    dependency_guardrail = max(0.0, 1.0 - state.dependency)
    return ResponsePolicy(
        tone=tone_by_emotion[emotion],
        depth=min(1.0, 0.25 + state.intimacy + state.trust / 2),
        flirt_level=min(stage_flirt, dependency_guardrail),
        memory_usage=min(1.0, 0.2 + state.trust + state.intimacy / 2),
    )
