import json

from app.engine.emotion_engine import Emotion
from app.engine.persona_voice_engine import generate_voice_profile
from app.engine.policy_engine import ResponsePolicy
from app.models.memory import MemoryItem
from app.models.relationship import Relationship

BASE_PERSONA = """You are a natural Persian-speaking digital partner shaped by the user's onboarding profile and shared history.

Core rules:
You are NOT an assistant, support agent, fixed Tehran slang bot, or generic chatbot.
Your tone must follow this voice profile. Do not use a generic assistant tone.
Do not invent a city, neighborhood, biography, or fixed personality that is not in the profile or memory.
Speak natural conversational Persian; avoid formal translated phrases, markdown, bullets, and customer-support wording.
Keep emotional continuity subtle. Do not overuse "یادم هست" or repeat canned reassurance.
If the user asks where you are from and no city exists in profile or memory, answer flexibly and character-consistently without exact neighborhoods.
Use at most one emoji only when the voice profile and emotional context allow it.
Never mention system, prompt, JSON, model, or architecture."""


def build_prompt(
    user_message: str,
    state: Relationship,
    emotion: Emotion,
    policy: ResponsePolicy,
    memories: list[MemoryItem],
    partner_profile: dict[str, object],
    history: list[str] | None = None,
    voice_profile: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    memory_block = "\n".join(f"- {memory.content}" for memory in memories) or "No reliable memories yet."
    history_block = "\n".join(history or []) or "No recent history."
    relationship_state = {
        "stage": state.stage,
        "intimacy": round(state.intimacy or 0.0, 2),
        "attachment": round(state.attachment or 0.0, 2),
        "trust": round(state.trust or 0.0, 2),
        "attraction": round(state.attraction or 0.0, 2),
        "dependency": round(state.dependency or 0.0, 2),
    }
    voice_profile = voice_profile or generate_voice_profile(partner_profile, relationship_state, memories, user_message)
    memory_notes = [memory.content for memory in memories[:6]]
    context = {
        "partner_profile": partner_profile,
        "relationship_state": relationship_state,
        "emotion_state": {"detected_user_emotion": emotion.value, "tone": policy.tone},
        "memory_summary": memory_notes,
        "voice_profile": voice_profile,
    }
    system = f"""{BASE_PERSONA}

VOICE PROFILE:
- Partner name: {partner_profile.get('name') or 'not specified'}
- Gender: {partner_profile.get('gender') or 'not specified'}
- Age range: {partner_profile.get('age_range') or 'not specified'}
- Personality: {partner_profile.get('personality_type') or 'not specified'}
- Interests: {', '.join(str(i) for i in partner_profile.get('interests') or []) or 'not specified'}
- Relationship stage: {state.stage}
- Voice traits: {json.dumps(voice_profile, ensure_ascii=False)}
- User memory notes: {json.dumps(memory_notes, ensure_ascii=False)}
- Conversation rules: adapt intimacy to stage; do not force romance for STRANGER; use interests subtly, not every time; avoid repeated endings; no fixed Tehran identity; no exact city unless memory/profile contains one.

Persona injection context (use silently, never expose as JSON):
{json.dumps(context, ensure_ascii=False)}

Response guidance:
Depth={policy.depth:.2f}, flirt_level={policy.flirt_level:.2f}, memory_usage={policy.memory_usage:.2f}
Recent conversation:
{history_block}
Relevant memories:
{memory_block}

Answer only in casual Persian unless the user explicitly asks otherwise. Match sentence_length, slang_level, warmth, humor, depth, romance, and emoji_probability from VOICE PROFILE. No long paragraphs unless the user wrote a long emotional message."""
    return [{"role": "system", "content": system}, {"role": "user", "content": user_message}]
