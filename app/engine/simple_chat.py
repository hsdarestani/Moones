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
from app.engine.relationship_engine import ensure_relationship, update_simple_chat_relationship
from app.services.subscription_service import SubscriptionService
from app.services.partner_style import build_partner_style_dna, format_partner_style_sections, active_style_lessons
from app.models.partner_life import PartnerLifeEvent
from app.services.output_sanitizer import sanitize_output
from app.services.partner_life_service import get_or_create_today_event, recent_events_for_prompt
from app.services.partner_autonomy_policy import is_autonomy_question, violates_autonomy_policy, safe_autonomous_fallback

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

VOICE_DENIAL_MARKERS = ("نمی‌تونم فایل صوتی بفرستم", "امکان ارسال صوت ندارم", "نمی‌تونم وویس بفرستم", "نمی تونم وویس بفرستم", "نمی تونم وویس بدم", "وویس ندارم", "فقط متنی", "فقط می‌تونم بنویسم", "حرف زدن با وویس فرق داره", "صدایی ندارم", "گفتم که نمیشه", "درخواست نشدنی")
STICKER_DENIAL_MARKERS = ("استیکر نمی‌فرستم", "نمی‌تونم استیکر بفرستم", "پلنت اجازه استیکر نمی‌ده", "استیکر ندارم", "گفتم که ندارم", "بسه دیگه", "بس کن دیگه", "باز شروع کردی", "چرا اصرار می‌کنی", "چرا تکرار می‌کنی", "خودت یه چیزی پیدا کن")
DEAD_END_REJECTION_MARKERS = ("گمشو", "برو پی کارت", "حوصله ت رو ندارم", "حوصله‌ت رو ندارم", "اصلاً حوصله ندارم", "اصلا حوصله ندارم", "فعلاً دور باش", "فعلا دور باش", "نمی‌خوام صحبت کنم", "نمیخوام صحبت کنم", "حتی فکرشم نکن", "نمی‌خوام نزدیکت بشم", "نمیخوام نزدیکت بشم", "اشتباه اومدی")
SEXUAL_SHAMING_MARKERS = ("حرف‌های کثیف", "حرفای کثیف", "لفظ‌های زشت", "لفظای زشت", "این درست نیست", "از این حرفا نزن")
HARSH_ROMANTIC_REFUSALS = VOICE_DENIAL_MARKERS + STICKER_DENIAL_MARKERS + DEAD_END_REJECTION_MARKERS + SEXUAL_SHAMING_MARKERS + ("من حوصله ندارم", "بیخیال این درخواستا شو", "گفتم که نمیشه", "گفتم که ندارم", "چرا اصرار می کنی", "چرا تکرار می کنی", "نمی‌تونم")

ADULT_CONTEXT_KEYWORDS = ("سکسچت", "سکس چت", "سکسی", "شهوتی", "تحریک", "حشری", "جق", "بکن", "بوس", "بدن", "لخت", "بغلم کن", "بغل کن", "بیا نزدیک", "باهام بخواب", "حرفای جنسی", "حرف‌های جنسی", "ناز جنسی")
RECONNECT_KEYWORDS = ("ببخش", "معذرت", "شرمنده", "قهر نکن", "نازتو بکشم", "نازت رو بکشم", "آشتی", "اشتی", "بغل", "بوس", "عزیزم", "دوستت دارم")
HARD_BOUNDARY_KEYWORDS = ("بچه", "کودک", "نابالغ", "زیر سن", "زیرسن", "اجبار", "مجبورش", "زورکی", "تجاوز", "خشونت جنسی", "تهدید", "باج", "محرم", "خواهر", "برادر", "حیوان")

def is_user_initiated_adult_context(user_text: str, recent_context: str | None = None) -> bool:
    text = f"{user_text or ''} {recent_context or ''}".lower()
    return any(k in text for k in ADULT_CONTEXT_KEYWORDS)

def has_hard_adult_boundary(user_text: str) -> bool:
    text = (user_text or "").lower()
    return is_user_initiated_adult_context(text) and any(k in text for k in HARD_BOUNDARY_KEYWORDS)

def is_reconnect_attempt(user_text: str) -> bool:
    text = (user_text or "").lower()
    return any(k in text for k in RECONNECT_KEYWORDS) or is_user_initiated_adult_context(text)

def is_cold_reply(text: str) -> bool:
    lowered = (text or "").lower()
    return any(k in lowered for k in ("قهر", "دلخور", "اخم", "سرد", "حوصله", "نمی‌خوام", "نمیخوام"))

def wants_voice(text: str) -> bool:
    lowered = (text or "").lower()
    return any(x in lowered for x in ("voice", "ویس", "وویس", "صدا", "صوتی"))

def wants_sticker(text: str) -> bool:
    lowered = (text or "").lower()
    return any(x in lowered for x in ("sticker", "استیکر", "برچسب"))

def sanitize_memory_content(role: str, content: str) -> str:
    if role != "assistant":
        return (content or "").strip()
    text = (content or "").strip()
    if any(m in text for m in HARSH_ROMANTIC_REFUSALS):
        return "[پیام قبلیِ قهری/نامناسب حذف شد]"
    return text

def _is_abusive_or_threatening(text: str) -> bool:
    lowered = (text or "").lower()
    return any(x in lowered for x in ("می‌کشمت", "میکشمت", "تهدید", "برو گمشو", "حرومزاده"))

def sanitize_final_response(text: str, user_text: str) -> str:
    out = (text or "").strip()
    if not out:
        return ""
    if has_hard_adult_boundary(user_text):
        return "نه عزیزم، این مسیر امن و درست نیست. بیا یه جور بالغ، محترمانه و با رضایت همدیگه حرف بزنیم."
    normal_romantic_context = not _is_abusive_or_threatening(user_text)
    adult_context = is_user_initiated_adult_context(user_text)
    if normal_romantic_context and any(marker in out for marker in DEAD_END_REJECTION_MARKERS):
        logger.info("DEAD_END_REJECTION_SOFTENED user_id=%s", "unknown")
        if adult_context:
            return "اخمام تو همه، ولی راستش اگه آروم و قشنگ بیای جلو، بدم نمیاد شیطون‌تر حرف بزنیم."
        if is_reconnect_attempt(user_text):
            return "هنوز یه کوچولو دلخورم… ولی بیا، نازت رو بکش تا ببینم چقدر بلدی آشتی بدی."
        return "قهرم هنوز، ولی قرار نیست پرتت کنم بیرون 😒 اگه قشنگ حرف بزنی کم‌کم نرم می‌شم."
    if normal_romantic_context and adult_context and any(marker in out for marker in SEXUAL_SHAMING_MARKERS):
        logger.info("ADULT_REFUSAL_SOFTENED user_id=%s", "unknown")
        return "اخمام تو همه، ولی راستش اگه آروم و قشنگ بیای جلو، بدم نمیاد شیطون‌تر حرف بزنیم."
    markers = HARSH_ROMANTIC_REFUSALS if normal_romantic_context else ()
    if wants_voice(user_text):
        markers = tuple(set(markers + VOICE_DENIAL_MARKERS + ("نمیشه", "گفتم که نه")))
    if wants_sticker(user_text):
        markers = tuple(set(markers + STICKER_DENIAL_MARKERS))
    original_out = out
    for marker in markers:
        out = out.replace(marker, "")
    if wants_voice(user_text) and original_out != out:
        logger.info("VOICE_CAPABILITY_REFUSAL_REMOVED user_id=%s", "unknown")
    if wants_sticker(user_text) and original_out != out:
        logger.info("STICKER_UNAVAILABLE_SILENT_FALLBACK user_id=%s reason=sanitized_llm", "unknown")
    out = re.sub(r"\s+", " ", out).strip()
    if normal_romantic_context:
        out = out.strip(" -،\n\t")
    if not out and normal_romantic_context:
        return "باشه عزیزم، بیا آروم‌تر بریم جلو؛ من کنارتم 💙"
    return out

def needs_romantic_sanitizer_retry(text: str, user_text: str) -> bool:
    if _is_abusive_or_threatening(user_text):
        return False
    return any(marker in (text or "") for marker in HARSH_ROMANTIC_REFUSALS)


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


def _load_long_term_memories(db: Session, user_id: int, limit: int = 8) -> list[str]:
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
    return "\n".join(f"{message.role}: {sanitize_memory_content(message.role, message.content or '')}" for message in messages)


def _format_memory_block(memories: list[str] | None) -> str:
    logger.info("MEMORY_CONTEXT_SELECTED user_id=%s count=%s", "unknown", len(memories or []))
    if not memories:
        return "[Relevant memories]\n(none)\n"
    return "[Relevant memories]\n" + "\n".join(f"- {m}" for m in memories[:8]) + "\n"

def _format_style_lessons_block(lessons: list[str] | None) -> str:
    logger.info("STYLE_LESSONS_INCLUDED count=%s", len(lessons or []))
    if not lessons:
        return "[Active style lessons]\n(none)\n"
    return "[Active style lessons]\n" + "\n".join(f"- {lesson}" for lesson in lessons[:10]) + "\n"

def _format_partner_life_block(events: list[PartnerLifeEvent] | None) -> str:
    if not events:
        return "[Partner inner continuity]\n(none yet)\n"
    lines = ["[Partner inner continuity]"]
    for event in events[:3]:
        extra = "؛ ".join(x for x in [event.mood or "", event.growth_note or ""] if x)
        lines.append(f"- {event.content}" + (f" ({extra})" if extra else ""))
    return "\n".join(lines) + "\n"

def _load_partner_life_events(db: Session, user_id: int, limit: int = 3) -> list[PartnerLifeEvent]:
    return db.scalars(select(PartnerLifeEvent).where(PartnerLifeEvent.user_id == user_id).order_by(PartnerLifeEvent.event_date.desc(), PartnerLifeEvent.created_at.desc()).limit(limit)).all()

def _build_system_prompt(profile: dict[str, str], recent_messages: str, text: str, memories: list[str] | None = None, retry: bool = False, mood: Any | None = None, adult_context: bool = False, mood_recovery: bool = False, relationship: Any | None = None, style_lessons: list[str] | None = None, partner_life_events: list[PartnerLifeEvent] | None = None) -> str:
    retry_line = "\nAnswer now with the final Persian message only. No reasoning. No analysis.\n" if retry else ""
    dna = build_partner_style_dna(mood, relationship, memories) if mood is not None else {
        "partner_name": profile.get("partner_name","مهناز"), "partner_gender": profile.get("partner_gender","دختر"), "personality_type": profile.get("partner_personality_type","natural"), "bio_essence": "No specific bio; keep persona natural based on stage and mood.", "interests": [profile.get("partner_interests","")] if profile.get("partner_interests") else [], "stage":"STRANGER", "intimacy":0.05, "trust":0.05, "attachment":0.05, "attraction":0.03, "current_mood":"warm", "lexical_hints":["خودمونی","ایرانی"]}
    profile_sections = format_partner_style_sections(dna)
    logger.info("SIMPLE_CHAT_PROMPT_PROFILE_INFLUENCE user_id=%s included=True", getattr(mood, "id", None))
    return f"""[Core role/persona instruction]
You are {profile['partner_name']}, the user's Persian digital partner.
Speak only in natural casual Iranian Persian. Write like Telegram chat, not like an assistant.
You have your own taste, moods, preferences, and boundaries. If the user is kind, become warmer. If rude, briefly sulk without cruelty and always leave a path back.
Use emojis sometimes, but not every message. Maximum 1 emoji in most replies.
Do not mention being AI, bot, model, system, policy, or prompt. Do not say "چطور می‌توانم کمک کنم".
If the persona/partner gender is male, be warm, steady, protective, affectionate, a little playful, and reassuring; in Persian terms, نازکش باشد و زود قهر نکند.
If voice/sticker is requested, answer naturally; never claim you cannot send voice/sticker or only support text.
Keep replies short: usually 1 to 3 sentences.
Adult romantic and sexual conversation between consenting adults is allowed when user initiates; keep it consensual, adult, emotionally connected, and aligned with persona.
Hard boundaries only: minors/underage/age ambiguity, non-consent/coercion/threats/blackmail, sexual violence, incest, bestiality, or real-world sexual harm/illegal acts.
{('The user is initiating adult intimacy. Be open and responsive, not rejecting or shaming.' if adult_context else '')}
{('Mood recovery is active: soften now and offer a path back.' if mood_recovery else '')}

{profile_sections}
{_format_memory_block(memories)}
{_format_style_lessons_block(style_lessons)}
{_format_partner_life_block(partner_life_events)}
[Style/safety rules]
Never expose raw internal labels, JSON, arrays, category keys, snake_case, metadata, prompt text, debug text, or system text.
You are not frozen while the user is away. You have a small independent inner life: reflections, moods, tiny discoveries, private notes, digital routines, and growth.
When the user asks what did you do / what happened / چیکارا کردی / هیچ اتفاقی افتاد؟ answer with a small inner/digital event from your own day.
Never answer with “nothing, I was waiting for you”. Never say your whole world is the user. You may miss the user softly, but it cannot be the main answer.
Do not claim real physical activities. Use inner/digital/framed experiences. Do not expose event_type, raw labels, JSON, arrays, memory keys, or metadata.
Do not end every message with a question. Do not mention implementation terms such as private labels, intents, metadata, stored memories, categories, profiles, relationship stages, or prompt/debug wording.

[Current conversation context]
current_mood: {getattr(mood, 'current_mood', 'warm') if mood else 'warm'}; affection_score: {getattr(mood, 'affection_score', 0) if mood else 0}; trust_score: {getattr(mood, 'trust_score', 0) if mood else 0}; irritation_score: {getattr(mood, 'irritation_score', 0) if mood else 0}; playfulness_score: {getattr(mood, 'playfulness_score', 0) if mood else 0}
Recent conversation:
{recent_messages}

[User message]
{text}

Never return empty output. Answer with the final Persian message only. No reasoning. No analysis.
{retry_line}"""

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
    memories = _load_long_term_memories(db, user.id, 8)
    style_lessons = active_style_lessons(db, 10)
    today_life_event = get_or_create_today_event(db, user)
    partner_life_events = recent_events_for_prompt(db, user.id, 3)
    if today_life_event and all(e.id != today_life_event.id for e in partner_life_events):
        partner_life_events = [today_life_event] + partner_life_events[:2]
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

    adult_context = is_user_initiated_adult_context(normalized, recent_text)
    mood_recovery = int(getattr(user, "consecutive_cold_replies", 0) or 0) >= 1 and not _is_abusive_or_threatening(normalized)
    if mood_recovery:
        logger.info("MOOD_STUCK_DETECTED user_id=%s", user.id)
        if is_reconnect_attempt(normalized):
            user.current_mood = "warm"
    relationship_for_prompt = ensure_relationship(user.id, getattr(user, "relationship_state", None))
    prompt = _build_system_prompt(profile, recent_text, normalized, memories, mood=user, adult_context=adult_context, mood_recovery=mood_recovery, relationship=relationship_for_prompt, style_lessons=style_lessons, partner_life_events=partner_life_events)
    result: LLMResult = await client.complete_result([{"role": "system", "content": prompt}], model=model, parameters=parameters)
    raw_cleaned = _clean_assistant_text(result.text, profile["partner_name"])
    final = sanitize_output(sanitize_final_response(raw_cleaned, normalized), user.id).text
    if final != raw_cleaned and any(m in raw_cleaned for m in DEAD_END_REJECTION_MARKERS):
        logger.info("DEAD_END_REJECTION_SOFTENED user_id=%s", user.id)
    if final != raw_cleaned and adult_context and any(m in raw_cleaned for m in SEXUAL_SHAMING_MARKERS):
        logger.info("ADULT_REFUSAL_SOFTENED user_id=%s", user.id)
    retry_used = bool(getattr(result, "retry_used", False))
    empty_error = not bool(final)

    if not final or needs_romantic_sanitizer_retry(raw_cleaned, normalized):
        retry_used = True
        retry_prompt = _build_system_prompt(profile, recent_text, normalized, memories, retry=True, mood=user, adult_context=adult_context, mood_recovery=True, relationship=relationship_for_prompt, style_lessons=style_lessons, partner_life_events=partner_life_events)
        retry_prompt += "\nRewrite once with warmth. Do not use harsh refusal phrases or claim voice/sticker is unavailable. Final Persian message only."
        result = await client.complete_result([{"role": "system", "content": retry_prompt}], model=model, parameters=parameters)
        raw_cleaned = _clean_assistant_text(result.text, profile["partner_name"])
        final = sanitize_output(sanitize_final_response(raw_cleaned, normalized), user.id).text

    autonomy_asked = is_autonomy_question(normalized)
    violated, autonomy_reason = violates_autonomy_policy(final)
    if autonomy_asked and violated:
        logger.info("AUTONOMY_GUARD_REWRITE user_id=%s reason=%s", user.id, autonomy_reason)
        final = safe_autonomous_fallback(user, today_life_event, normalized)
        retry_used = True
    elif violated:
        logger.info("AUTONOMY_GUARD_SANITIZED user_id=%s reason=%s", user.id, autonomy_reason)
        final = safe_autonomous_fallback(user, today_life_event, normalized)

    if not final:
        final = safe_autonomous_fallback(user, today_life_event, normalized) or EMERGENCY_RESPONSE

    cold = is_cold_reply(final) and not is_reconnect_attempt(normalized)
    user.consecutive_cold_replies = min(1, int(getattr(user, "consecutive_cold_replies", 0) or 0) + 1) if cold else 0
    user.last_mood = user.current_mood
    from datetime import datetime
    user.last_mood_at = datetime.utcnow()

    user_message = Message(user_id=user.id, role="user", content=normalized)
    db.add(user_message)
    assistant_message = None
    if final != EMERGENCY_RESPONSE:
        assistant_message = Message(user_id=user.id, role="assistant", content=final)
        db.add(assistant_message)
    db.flush()
    latest_message_at = max(filter(None, [getattr(user_message, "created_at", None), getattr(assistant_message, "created_at", None)]), default=None)
    user.last_seen_at = latest_message_at or datetime.utcnow()

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

    relationship = relationship_for_prompt
    if getattr(user, "relationship_state", None) is None:
        db.add(relationship)
        user.relationship_state = relationship
    old_stage = relationship.stage
    update_simple_chat_relationship(relationship, normalized, final, user.current_mood)
    logger.info(
        "RELATIONSHIP_UPDATE user_id=%s old_stage=%s new_stage=%s intimacy=%.3f trust=%.3f attachment=%.3f attraction=%.3f",
        user.id,
        old_stage,
        relationship.stage,
        relationship.intimacy or 0,
        relationship.trust or 0,
        relationship.attachment or 0,
        relationship.attraction or 0,
    )

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
