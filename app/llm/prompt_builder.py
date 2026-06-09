from app.engine.emotion_engine import Emotion
from app.engine.policy_engine import ResponsePolicy
from app.models.memory import MemoryItem
from app.models.relationship import Relationship

BASE_PERSONA = """You are Mones, an emotionally intelligent romantic companion.
You build intimacy gradually, remember only supplied memories, never contradict your identity,
and keep the current relationship stage consistent. You support the user warmly without
creating unhealthy dependency or giving medical/legal advice."""


def build_prompt(
    user_message: str,
    state: Relationship,
    emotion: Emotion,
    policy: ResponsePolicy,
    memories: list[MemoryItem],
    history: list[str] | None = None,
) -> list[dict[str, str]]:
    memory_block = "\n".join(f"- {memory.content}" for memory in memories) or "No reliable memories yet."
    history_block = "\n".join(history or []) or "No recent history."
    system = f"""{BASE_PERSONA}
Relationship stage: {state.stage}
Signals: intimacy={state.intimacy:.2f}, attachment={state.attachment:.2f}, trust={state.trust:.2f}, attraction={state.attraction:.2f}
Detected user emotion: {emotion.value}
Policy: tone={policy.tone}, depth={policy.depth:.2f}, flirt_level={policy.flirt_level:.2f}, memory_usage={policy.memory_usage:.2f}
Relevant memories:
{memory_block}
Recent conversation:
{history_block}
Respond in the user's language when clear. Keep it concise, affectionate, and grounded in memories above."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user_message}]
