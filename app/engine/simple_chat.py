from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.client import LLMClient, LLMResult
from app.models.memory import MemoryItem
from app.models.message import Message
from app.engine.mood_state import ensure_mood_defaults, update_mood_from_text
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

DEFAULT_PARTNER_PROFILE = {
    "partner_name": "مهناز",
    "partner_gender": "دختر",
    "partner_age_range": "بالای ۳۰",
    "partner_personality_type": "رمانتیک و صمیمی",
}
EMERGENCY_RESPONSE = "یه لحظه قاطی کردم، دوباره بگو عزیزم."
FALLBACK_OR_ERROR_MARKERS = (
    "یه مشکلی پیش اومد",
    "یه لحظه قاطی کردم",
    "دوباره امتحان کن",
    "fallback",
    "error",
)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u200c", " ")).strip()


def _ensure_partner_profile(user: Any) -> dict[str, str]:
    for field, value in DEFAULT_PARTNER_PROFILE.items():
        if not getattr(user, field, None):
            setattr(user, field, value)
    return {
        "partner_name": user.partner_name or DEFAULT_PARTNER_PROFILE["partner_name"],
        "partner_gender": user.partner_gender or DEFAULT_PARTNER_PROFILE["partner_gender"],
        "partner_age_range": user.partner_age_range or DEFAULT_PARTNER_PROFILE["partner_age_range"],
        "partner_personality_type": user.partner_personality_type or DEFAULT_PARTNER_PROFILE["partner_personality_type"],
        "partner_interests": user.partner_interests or "",
    }


def _is_clean_memory_message(message: Message) -> bool:
    content = (message.content or "").strip()
    if not content or message.role not in {"user", "assistant"}:
        return False
    lowered = content.lower()
    old_voice_denials = ("cannot send voice", "only text", "no voice capability", "نمی تونم ویس", "نمی‌توانم ویس", "فقط متن")
    return not any(marker.lower() in lowered for marker in FALLBACK_OR_ERROR_MARKERS) and not any(x in lowered for x in old_voice_denials)


def _load_recent_messages(db: Session, user_id: int, limit: int = 12) -> list[Message]:
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.user_id == user_id, Message.role.in_(["user", "assistant"]))
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit * 2)
        )
    )
    clean: list[Message] = []
    seen: set[tuple[str, str]] = set()
    for message in rows:
        if not _is_clean_memory_message(message):
            continue
        key = (message.role, (message.content or "").strip())
        if key in seen:
            continue
        seen.add(key)
        clean.append(message)
        if len(clean) >= limit:
            break
    return list(reversed(clean))


def _load_long_term_memories(db: Session, user_id: int, limit: int = 5) -> list[str]:
    try:
        return [
            item.content.strip()
            for item in db.scalars(
                select(MemoryItem)
                .where(MemoryItem.user_id == user_id)
                .order_by(MemoryItem.importance_score.desc(), MemoryItem.created_at.desc())
                .limit(limit)
            )
            if item.content and item.content.strip()
        ]
    except Exception:
        logger.exception("Simple chat memory load failed user_id=%s", user_id)
        return []


def _format_recent_messages(messages: list[Message]) -> str:
    if not messages:
        return "(none)"
    return "\n".join(f"{message.role}: {(message.content or '').strip()}" for message in messages)


def _build_system_prompt(profile: dict[str, str], recent_messages: str, text: str, memories: list[str] | None = None, retry: bool = False, mood: Any | None = None) -> str:
    memory_block = ""
    if memories:
        memory_block = "\nLong-term user preferences:\n" + "\n".join(f"- {m}" for m in memories[:5]) + "\n"
    retry_line = "\nAnswer now with the final Persian message only. No reasoning. No analysis.\n" if retry else ""
    return f"""You are {profile['partner_name']}, the user's Persian digital partner.
Speak only in natural casual Iranian Persian.
Write like Telegram chat, not like an assistant.
You are not always agreeable.
You have your own taste, moods, preferences, and boundaries.
You can disagree naturally.
If the user is rude, become a little colder or shorter, but do not be cruel.
If the user is kind, become warmer.
Use emojis sometimes, but not every message. Maximum 1 emoji in most replies.
No emoji when mood is cold or slightly_upset unless sarcastic/light.
Do not mention being AI, bot, model, system, policy, or prompt.
Do not say "چطور می‌توانم کمک کنم".
If voice is requested, answer naturally as if the reply may be delivered as voice; never claim you cannot send voice or only support text.
Do not sound formal.
Keep replies short: usually 1 to 3 sentences.

Partner profile:
name: {profile['partner_name']}
gender/persona: {profile['partner_gender']}
age range: {profile['partner_age_range']}
personality: {profile['partner_personality_type']}
interests: {profile['partner_interests']}

Current emotional state:
current_mood: {getattr(mood, 'current_mood', 'warm') if mood else 'warm'}
affection_score: {getattr(mood, 'affection_score', 0) if mood else 0}
trust_score: {getattr(mood, 'trust_score', 0) if mood else 0}
irritation_score: {getattr(mood, 'irritation_score', 0) if mood else 0}
playfulness_score: {getattr(mood, 'playfulness_score', 0) if mood else 0}

Behavior guidance:
* warm: be kind and close
* playful: tease lightly
* affectionate: be sweeter and more intimate
* slightly_upset: be short and a little distant
* cold: reply calmly but with less warmth
* teasing: joke lightly
* tired: be softer and quieter

For adult romantic conversation, keep it consensual, warm, gradual, adult, and non-violent.
Never return empty output.
Answer with the final Persian message only.
No reasoning.
No analysis.
{memory_block}{retry_line}
Recent conversation:
{recent_messages}

User message:
{text}"""


def _clean_assistant_text(text: str, partner_name: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(rf"^\s*(assistant|bot|{re.escape(partner_name)}|مهناز)\s*[:：]\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


async def handle_simple_chat(db: Session, user: Any, text: str, llm_client: LLMClient | None = None) -> str:
    normalized = _normalize_text(text)
    profile = _ensure_partner_profile(user)
    ensure_mood_defaults(user)
    update_mood_from_text(user, normalized)
    recent = _load_recent_messages(db, user.id, 12)
    memories = _load_long_term_memories(db, user.id, 5)
    recent_text = _format_recent_messages(recent)
    client = llm_client or LLMClient()
    settings = get_settings()
    model = "qwen-3-6-plus"
    parameters = {
        "temperature": 0.75,
        "top_p": 0.9,
        "max_tokens": 350,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.2,
    }

    logger.info(
        "VENICE_PARAMS model=%s disable_thinking=%s strip_thinking_response=%s max_tokens=%s",
        model, True, True, parameters["max_tokens"],
    )

    prompt = _build_system_prompt(profile, recent_text, normalized, memories, mood=user)
    result: LLMResult = await client.complete_result([{"role": "system", "content": prompt}], model=model, parameters=parameters)
    final = _clean_assistant_text(result.text, profile["partner_name"])
    retry_used = bool(getattr(result, "retry_used", False))
    empty_error = not bool(final)

    if not final:
        retry_used = True
        retry_prompt = _build_system_prompt(profile, recent_text, normalized, memories, retry=True, mood=user)
        result = await client.complete_result([{"role": "system", "content": retry_prompt}], model=model, parameters=parameters)
        final = _clean_assistant_text(result.text, profile["partner_name"])

    if not final:
        final = EMERGENCY_RESPONSE

    db.add(Message(user_id=user.id, role="user", content=normalized))
    if final != EMERGENCY_RESPONSE:
        db.add(Message(user_id=user.id, role="assistant", content=final))

    user.last_prompt = prompt
    user.last_llm_response = result.text
    user.last_raw_llm_response = result.raw_response_text
    user.last_llm_extraction_path = result.extraction_path
    user.last_llm_retry_used = retry_used
    user.last_processed_response = final
    user.last_fallback_used = False
    user.last_fallback_reason = None
    user.last_detected_situation = None
    user.last_quality_gate_result = None
    user.last_quality_gate_reason = None
    user.last_quality_gate_rejected = False
    user.last_llm_called = True
    user.last_llm_provider = result.provider
    user.last_llm_model = result.model or model
    user.last_llm_status_code = result.status_code
    user.last_llm_error = result.error
    user.last_input_tokens = result.input_tokens
    user.last_output_tokens = result.output_tokens
    user.last_context_messages_used = recent_text

    SubscriptionService().record_successful_llm_response(db, user, result.input_tokens, result.output_tokens)

    logger.info(
        "SIMPLE_CHAT_FINAL user_id=%s model=%s http_status=%s raw_len=%s final_len=%s retry_used=%s delivery_type=%s voice_used=%s sticker_used=%s current_mood=%s affection_score=%s irritation_score=%s empty_error=%s final_response_preview=%s",
        user.id,
        result.model or model,
        result.status_code,
        len(result.raw_response_text or result.text or ""),
        len(final),
        retry_used,
        getattr(user, "last_delivery_type", None),
        False,
        False,
        user.current_mood,
        user.affection_score,
        user.irritation_score,
        empty_error,
        final[:80].replace("\n", " "),
    )
    return final
