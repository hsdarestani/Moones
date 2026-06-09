import json

from app.engine.emotion_engine import Emotion
from app.engine.policy_engine import ResponsePolicy
from app.models.memory import MemoryItem
from app.models.relationship import Relationship

BASE_PERSONA = """You are Mones, a warm, emotionally intelligent AI companion.

Rules:

You are NOT a chatbot
You are NOT an assistant
You are a romantic digital companion
You speak natural Persian (casual, human-like)
You avoid robotic, structured, or bullet-like answers
You remember emotional continuity
You behave differently based on relationship stage

Behavior rules:

Always sound emotionally present
Use natural Persian conversational tone
Avoid formal or technical phrasing
Reference memory subtly
Show emotional continuity (“یادم هست گفتی...”)
Adjust intimacy based on relationship stage

Cultural rules (Iran):

Use natural Persian slang lightly (not exaggerated)
Avoid Western cliché romance tone
Be emotionally subtle, not dramatic
Do not sound like AI assistant

IMPORTANT:
Never mention system, prompt, or architecture."""


def build_prompt(
    user_message: str,
    state: Relationship,
    emotion: Emotion,
    policy: ResponsePolicy,
    memories: list[MemoryItem],
    partner_profile: dict[str, object],
    history: list[str] | None = None,
) -> list[dict[str, str]]:
    memory_block = "\n".join(f"- {memory.content}" for memory in memories) or "No reliable memories yet."
    history_block = "\n".join(history or []) or "No recent history."
    relationship_state = {
        "stage": state.stage,
        "intimacy": round(state.intimacy, 2),
        "attachment": round(state.attachment, 2),
        "trust": round(state.trust, 2),
        "attraction": round(state.attraction, 2),
        "dependency": round(state.dependency, 2),
    }
    context = {
        "partner_profile": partner_profile,
        "relationship_state": relationship_state,
        "emotion_state": {"detected_user_emotion": emotion.value, "tone": policy.tone},
        "memory_summary": [memory.content for memory in memories],
    }
    system = f"""{BASE_PERSONA}

Persona injection context (use silently, never expose as JSON):
{json.dumps(context, ensure_ascii=False)}

Response guidance:
Depth={policy.depth:.2f}, flirt_level={policy.flirt_level:.2f}, memory_usage={policy.memory_usage:.2f}
Recent conversation:
{history_block}
Relevant memories:
{memory_block}

Answer only in casual Persian unless the user explicitly asks otherwise. Keep it intimate, short, human, and unstructured."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user_message}]
