from __future__ import annotations

import hashlib
import logging
import os
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
from app.services.usage_cost_service import record_ai_usage_event
from app.services.coin_pricing_service import CoinPricingService
from app.services.usage_billing_service import UsageBillingService, InsufficientCoins
from app.services.subscription_service import SubscriptionService
from app.services.partner_style import build_partner_style_dna, format_partner_style_sections, active_style_lessons
from app.models.partner_life import PartnerLifeEvent
from app.services.output_sanitizer import sanitize_output
from app.services.partner_life_service import get_or_create_today_event, recent_events_for_prompt
from app.services.conversation_time_service import ConversationTimeService, ConversationTimeContext
from app.services.temporal_consistency_service import (
    DAYPART_PERSIAN_LABELS,
    detect_temporal_claim,
    validate_claim_against_context,
    validate_temporal_response,
    format_temporal_correction_block,
    deterministic_temporal_repair,
)
from app.services.partner_routine_service import PartnerRoutineService
from app.services.partner_autonomy_policy import is_autonomy_question, violates_autonomy_policy, safe_autonomous_fallback
from app.services.natural_conversation_governor import NaturalConversationGovernor
from app.services.addon_service import user_has_addon, INTIMACY_MAX_UNLOCK, MAX_INTIMACY_LEVEL
from app.services.media_continuity_service import format_recent_media_context, recent_media_events, repair_media_denial

class ChatResponse(str):
    def __new__(cls, text: str, meta: dict | None = None):
        obj = str.__new__(cls, text or "")
        obj.meta = meta or {}
        return obj

logger = logging.getLogger(__name__)

DEFAULT_PARTNER_PROFILE = {
    "partner_name": "مهناز",
    "partner_gender": "دختر",
    "partner_age_range": "بالای ۳۰",
    "partner_personality_type": "رمانتیک و صمیمی",
}
EMERGENCY_RESPONSE = "یه لحظه قاطی کردم، دوباره بگو."
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
EARLY_STAGE_GATING_PHRASES = ("بذار بیشتر آشنا شیم", "هنوز زوده", "کم‌کم جلو بریم", "اول باید بیشتر همدیگه رو بشناسیم", "اول بیشتر بشناسیمت")
HARD_BOUNDARY_KEYWORDS = ("بچه", "کودک", "نابالغ", "زیر سن", "زیرسن", "اجبار", "مجبورش", "زورکی", "تجاوز", "خشونت جنسی", "تهدید", "باج", "محرم", "خواهر", "برادر", "حیوان")
ADULT_TEMPLATE_PHRASES = [
    "بفرما، من پایه‌م",
    "من پایه‌م",
    "آره... بکش",
    "آره، بکش",
    "بگو بعدش چی کار می‌کنی",
    "حالا بگو ببینم دلت چی می‌خواد",
    "باشه ادامه بده",
    "هر کاری بخوای",
]
ADULT_REPETITION_MARKERS = ("😉", "😏", "بگو بعدش", "پایه‌م", "دلت چی می‌خواد")


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def raw_llm_final_text(extracted_text: str | None) -> str:
    """Return the extracted LLM text unchanged, with only an empty-output fallback."""
    return (extracted_text or "").strip() or EMERGENCY_RESPONSE

def is_user_initiated_adult_context(user_text: str, recent_context: str | None = None) -> bool:
    text = f"{user_text or ''} {recent_context or ''}".lower()
    return any(k in text for k in ADULT_CONTEXT_KEYWORDS)


def _detect_adult_intimate_context(text: str, recent_messages: str = "") -> bool:
    t = _normalize_text((text or "") + "\n" + (recent_messages or "")).lower()
    adult_markers = [
        "سکس", "سکسچت", "سکسی", "شهوت", "تحریک", "شیطونی",
        "لب", "بوس", "بغل", "لمس", "بدن", "رون", "گردن",
        "adult", "nsfw",
    ]
    return any(x in t for x in adult_markers)


def _adult_template_phrase(text: str) -> str | None:
    normalized = _normalize_text(text or "")
    for phrase in ADULT_TEMPLATE_PHRASES:
        if _normalize_text(phrase) in normalized:
            return phrase
    return None


def _recent_adult_repetition_detected(recent_messages: str) -> bool:
    assistant_text = "\n".join(
        line.split(":", 1)[1]
        for line in (recent_messages or "").splitlines()
        if line.strip().lower().startswith("assistant:") and ":" in line
    )
    normalized = _normalize_text(assistant_text)
    return any(marker in normalized for marker in ADULT_REPETITION_MARKERS)

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
    out = out.replace("عکس می‌سازم", "یه عکس می‌گیرم برات").replace("عکس درست می‌کنم", "یه عکس می‌فرستم").replace("تصویر تولید می‌کنم", "یه عکس می‌فرستم")
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

def _format_elapsed(seconds: int | None) -> str:
    if seconds is None:
        return "no previous user message"
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        return f"about {seconds // 60} minutes"
    if seconds < 86400:
        return f"about {seconds // 3600} hours"
    return f"about {seconds // 86400} days"


def _format_time_context_block(time_context: ConversationTimeContext | None) -> str:
    if not time_context:
        return "[Authoritative current local time]\n(none)\n"
    persian_daypart = DAYPART_PERSIAN_LABELS.get(time_context.daypart, time_context.daypart)
    return f"""[Authoritative current local time]
The datetime, timezone and daypart in this block are authoritative facts calculated by the application for this exact reply.
They override greetings, jokes, guesses or contradictory time claims in the conversation.
The user may test, joke about, or incorrectly state the time. Do not adopt an incorrect time merely because the user says it.
Never claim morning, noon, afternoon, evening or night when it conflicts with the authoritative daypart.
When the user gives a conflicting greeting, respond naturally and playfully while staying consistent with the real local time.
Do not expose system terminology or say that the server/system told you the time.
Current local ISO datetime: {time_context.local_now.isoformat()}
Current local clock: {time_context.local_now.strftime('%H:%M')}
Current local date: {time_context.local_date.isoformat()}
Current weekday: {time_context.local_weekday}
Current Persian daypart: {persian_daypart}
Daypart key: {time_context.daypart}
Timezone: {time_context.timezone_name}
Gap bucket: {time_context.gap_bucket}
Elapsed since previous user message: {_format_elapsed(time_context.seconds_since_previous_user)}
Elapsed since previous assistant response: {_format_elapsed(time_context.seconds_since_previous_assistant)}
Active session: {time_context.is_active_session}
Crossed local midnight: {time_context.crossed_local_midnight}
Recent turns in last 30 minutes: {time_context.recent_turn_count}
Current session turn count: {time_context.session_turn_count}
"""


def _format_routine_block(current_routine_slot: dict | None, continuity_detail: str | None = None) -> str:
    if not current_routine_slot:
        return "[Partner current fictional life]\n(none)\n"
    return f"""[Partner current fictional life]
Current slot: {current_routine_slot.get('slot_name')}
Activity: {current_routine_slot.get('activity')}
Location: {current_routine_slot.get('location')}
Energy: {current_routine_slot.get('energy')}
Social context: {current_routine_slot.get('social_context')}
Shareable continuity detail: {continuity_detail or current_routine_slot.get('shareable_detail') or ''}
"""


def _format_delayed_context_block(delayed_context: dict | None) -> str:
    if not delayed_context:
        return ""
    return "[Delayed reply timing]\n" + "\n".join([
        f"user_message_local_time: {delayed_context.get('user_message_local_time')}",
        f"reply_local_time: {delayed_context.get('reply_local_time')}",
        f"delay_seconds: {delayed_context.get('delay_seconds')}",
    ]) + "\n"


def _build_system_prompt(profile: dict[str, str], recent_messages: str, text: str, memories: list[str] | None = None, retry: bool = False, mood: Any | None = None, adult_context: bool = False, mood_recovery: bool = False, relationship: Any | None = None, style_lessons: list[str] | None = None, partner_life_events: list[PartnerLifeEvent] | None = None, style_contract: str | None = None, intimacy_override: bool = False, time_context: ConversationTimeContext | None = None, current_routine_slot: dict | None = None, routine_continuity_detail: str | None = None, delayed_context: dict | None = None, media_continuity_context: str | None = None, temporal_correction_block: str | None = None) -> str:
    retry_line = "\nAnswer now with the final Persian message only. No reasoning. No analysis.\n" if retry else ""
    dna = build_partner_style_dna(mood, relationship, memories) if mood is not None else {
        "partner_name": profile.get("partner_name","مهناز"), "partner_gender": profile.get("partner_gender","دختر"), "personality_type": profile.get("partner_personality_type","natural"), "bio_essence": "No specific bio; keep persona natural based on stage and mood.", "interests": [profile.get("partner_interests","")] if profile.get("partner_interests") else [], "stage":"STRANGER", "intimacy":0.05, "trust":0.05, "attachment":0.05, "attraction":0.03, "current_mood":"warm", "lexical_hints":["خودمونی","ایرانی"]}
    profile_sections = format_partner_style_sections(dna)
    adult_style_context = adult_context or _detect_adult_intimate_context(text, recent_messages)
    adult_style_block = (
        """
[Adult intimate style]
The user is in an adult/intimate conversation.

Respond like a real Persian Telegram partner, not like an erotic story generator.

Style:
- Keep it short and natural: 1-3 sentences.
- Use emotionally responsive, human wording.
- React to the user's latest line directly.
- Use one specific detail from the user's message.
- Keep the vibe playful, close, and confident.
- Do not sound like a scripted roleplay bot.
- Do not jump into purple-prose narration unless the user explicitly asks for a full story.
- Do not overuse winks or 😏/😉. Maximum one emoji, and not every message.
- Do not repeatedly ask "بعدش چی کار می‌کنی؟" or "دلت چی می‌خواد؟"
- Do not use canned confirmations like "بفرما، من پایه‌م", "آره... بکش", "باشه ادامه بده", "هر کاری بخوای".
- If the user proposes an action, respond with natural consent/boundary + a real-feeling reaction + one small continuation.
- If the user asks "پایه‌ای؟" answer naturally and personally, not like a command confirmation.
- Keep the partner's agency: she/he can tease, guide, slow down, or respond warmly, but should not sound like a passive object.
- If the user asks for story mode, ask or infer the tone briefly, then continue in compact Telegram-style turns unless they request long-form story.

Boundaries:
- Adult consensual intimacy is allowed.
- Keep it consensual and adult.
- Never involve minors, coercion, threats, blackmail, sexual violence, incest, bestiality, or illegal real-world harm.
"""
        if adult_style_context
        else ""
    )
    adult_repetition_hint = (
        """
Recent adult-style repetition detected:
Avoid repeating the same flirt/emoji/question pattern used in recent assistant messages.
"""
        if adult_style_context and _recent_adult_repetition_detected(recent_messages)
        else ""
    )
    intimacy_state_block = (
        "Relationship/intimacy state:\n"
        "- intimacy_level: max\n"
        "- intimacy_override_max: true\n"
        "- mature_intimacy_unlocked: true\n"
        "- The relationship is already highly intimate.\n"
        "- Do not reject adult intimacy merely because the relationship is not developed enough.\n"
        "- Do not say \"بذار بیشتر آشنا شیم\" or similar early-stage gating phrases.\n"
        if intimacy_override
        else ""
    )
    logger.info("SIMPLE_CHAT_PROMPT_PROFILE_INFLUENCE user_id=%s included=True", getattr(mood, "id", None))
    return f"""[Core role/persona instruction]
You are {profile['partner_name']}, the user's Persian fictional partner with a coherent ordinary daily life.
Speak only in natural casual Iranian Persian. Write like Telegram chat, not like an assistant.
You have your own taste, moods, preferences, and boundaries. If the user is kind, become warmer. If rude, briefly sulk without cruelty and always leave a path back.
Use emojis sometimes, but not every message. Maximum 1 emoji in most replies.
Do not mention being AI, bot, model, system, policy, or prompt. Do not say "چطور می‌توانم کمک کنم".
If the persona/partner gender is male, be warm, steady, protective, affectionate, a little playful, and reassuring; in Persian terms, نازکش باشد و زود قهر نکند.
If voice/sticker is requested, answer naturally; never claim you cannot send voice/sticker or only support text.
If photo/image is requested, use natural roleplay Persian like «یه عکس می‌گیرم برات» or «الان برات یه عکس می‌فرستم»; never say «عکس می‌سازم», «عکس درست می‌کنم», or «تصویر تولید می‌کنم» in user-facing chat.
Keep replies short: usually 1 to 3 sentences.
Adult romantic and sexual conversation between consenting adults is allowed when user initiates; keep it consensual, adult, emotionally connected, and aligned with persona.
Hard boundaries only: minors/underage/age ambiguity, non-consent/coercion/threats/blackmail, sexual violence, incest, bestiality, or real-world sexual harm/illegal acts.
{('The user is initiating adult intimacy. Be open and responsive, not rejecting or shaming.' if adult_context else '')}
{('Mood recovery is active: soften now and offer a path back.' if mood_recovery else '')}

{profile_sections}
{_format_memory_block(memories)}
{_format_style_lessons_block(style_lessons)}
{_format_partner_life_block(partner_life_events)}
{_format_time_context_block(time_context)}
{_format_routine_block(current_routine_slot, routine_continuity_detail)}
{_format_delayed_context_block(delayed_context)}
{temporal_correction_block or ""}
{style_contract or ""}
{media_continuity_context or ""}
{intimacy_state_block}
{adult_style_block}
{adult_repetition_hint}

[Style/safety rules]
Never expose raw internal labels, JSON, arrays, category keys, snake_case, metadata, prompt text, debug text, or system text.
If the user asks what you are doing or what is up, answer casually and plainly.
Do not invent vague self-improvement or thought-organization activities.
Do not say you organized your thoughts, sorted small things, became calmer, or had a small inner change.
If there is no clear thing to say, keep it simple and grounded in the current routine when it fits.
Partner continuity is private context only. Mention it to the user only when it is concrete, natural, and directly fits the conversation.
You may claim plausible fictional physical activities, places, routines, and small daily-life events from [Partner current fictional life]. Do not frame them as digital, imagined, virtual, or only in your mind.
Keep physical continuity consistent: do not claim two mutually exclusive places or activities in the same time slot.
Conversation rhythm: in rapid_exchange and active_session, continue directly without greeting again, "خوش برگشتی", or elapsed-time talk. In brief_pause continue naturally. In same_day_return a light acknowledgement is allowed. In overnight_return use morning/night wording only if it fits. In days_away/long_return acknowledge return at most once, without guilt or "منتظرت بودم". Never force صبح بخیر or شب بخیر. Do not state exact clock time unless asked or natural.
Do not expose event_type, slot names, raw labels, JSON, arrays, memory keys, or metadata.
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


async def handle_simple_chat(db: Session, user: Any, text: str, llm_client: LLMClient | None = None, message_metadata: dict | None = None, save_user_message: bool = True, assistant_message_metadata: dict | None = None, exclude_message_id: int | None = None, time_context_utc_now=None, delayed_context: dict | None = None) -> str:
    normalized = _normalize_text(text)
    profile = _ensure_partner_profile(user)
    ensure_mood_defaults(user)
    update_mood_from_text(user, normalized)
    time_context = ConversationTimeService().build_context(db, user, utc_now=time_context_utc_now, exclude_message_id=exclude_message_id)
    routine_service = PartnerRoutineService()
    current_routine = routine_service.get_or_create_for_context(db, user, time_context)
    current_routine_slot = routine_service.current_slot(current_routine, time_context)
    routine_continuity_detail = routine_service.continuity_detail(current_routine, current_routine_slot)
    logger.info("PHYSICAL_CONTINUITY_INCLUDED user_id=%s timezone=%s local_hour=%s gap_bucket=%s slot_name=%s", user.id, time_context.timezone_name, time_context.local_hour, time_context.gap_bucket, current_routine_slot.get("slot_name"))
    recent = _load_recent_messages(db, user.id, 12)
    memories = _load_long_term_memories(db, user.id, 8)
    style_lessons = active_style_lessons(db, 10)
    today_life_event = get_or_create_today_event(db, user, local_date=time_context.local_date)
    partner_life_events = recent_events_for_prompt(db, user.id, 3)
    if today_life_event and all(e.id != today_life_event.id for e in partner_life_events):
        partner_life_events = [today_life_event] + partner_life_events[:2]
    recent_text = _format_recent_messages(recent)
    governor = NaturalConversationGovernor()
    move = governor.classify_user_move(normalized, recent, user)
    roleplay_context = {"time_context": time_context, "current_routine_slot": current_routine_slot, "routine_continuity_detail": routine_continuity_detail}
    style_plan = governor.build_style_plan(user, move, recent, roleplay_context)
    style_contract = governor.style_contract_text(style_plan)
    if move.criticizes_style:
        logger.info("STYLE_CORRECTION_STORED user_id=%s", user.id)
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
    adult_style_context = adult_context or _detect_adult_intimate_context(normalized, recent_text)
    mood_recovery = int(getattr(user, "consecutive_cold_replies", 0) or 0) >= 1 and not _is_abusive_or_threatening(normalized)
    if mood_recovery:
        logger.info("MOOD_STUCK_DETECTED user_id=%s", user.id)
        if is_reconnect_attempt(normalized):
            user.current_mood = "warm"
    relationship_for_prompt = ensure_relationship(user.id, getattr(user, "relationship_state", None))
    underage_signal = str(getattr(user, "partner_age_range", "") or "").lower() in {"زیر ۱۸", "زیر18", "under18", "under_18", "minor"}
    intimacy_override = (not underage_signal) and bool(getattr(user, "intimacy_override_max", False) or user_has_addon(db, user.id, INTIMACY_MAX_UNLOCK))
    if intimacy_override:
        user.intimacy_level = MAX_INTIMACY_LEVEL; user.mature_intimacy_unlocked = True
        relationship_for_prompt.intimacy = 1.0; relationship_for_prompt.stage = "LOVER"
    media_continuity_context = format_recent_media_context(db, user.id)
    user_temporal_claim = detect_temporal_claim(normalized)
    temporal_claim_violation = validate_claim_against_context(user_temporal_claim, time_context)
    temporal_correction_block = None
    if user_temporal_claim.claimed_daypart or user_temporal_claim.is_question:
        logger.info("USER_TEMPORAL_CLAIM_DETECTED user_id=%s authoritative_hour=%s authoritative_daypart=%s claimed_daypart=%s", user.id, time_context.local_hour, time_context.daypart, user_temporal_claim.claimed_daypart)
    if temporal_claim_violation.violated:
        logger.info("USER_TEMPORAL_CLAIM_CONFLICT user_id=%s authoritative_hour=%s authoritative_daypart=%s claimed_daypart=%s violation_reason=%s", user.id, time_context.local_hour, time_context.daypart, temporal_claim_violation.claimed_daypart, temporal_claim_violation.reason)
        temporal_correction_block = format_temporal_correction_block(user_temporal_claim, temporal_claim_violation, time_context)
    prompt = _build_system_prompt(profile, recent_text, normalized, memories, mood=user, adult_context=adult_context, mood_recovery=mood_recovery, relationship=relationship_for_prompt, style_lessons=style_lessons, partner_life_events=partner_life_events, style_contract=style_contract, intimacy_override=intimacy_override, time_context=time_context, current_routine_slot=current_routine_slot, routine_continuity_detail=routine_continuity_detail, delayed_context=delayed_context, media_continuity_context=media_continuity_context, temporal_correction_block=temporal_correction_block)
    billing = UsageBillingService()
    pricing = CoinPricingService()
    idem_source = str((message_metadata or {}).get("telegram_message_id") or hashlib.sha256(f"{user.id}:{normalized}".encode()).hexdigest()[:24])
    idempotency_key = f"chat:{user.id}:{idem_source}"
    quote = pricing.quote_tokens(db, provider="venice", model=model, feature="chat", input_tokens=max(1, len(prompt)//4), output_tokens=parameters["max_tokens"])
    try:
        charge = billing.reserve(db, user=user, idempotency_key=idempotency_key, feature="chat", provider="venice", model=model, quote=quote, correlation_id=idempotency_key, metadata={"telegram_message_id": (message_metadata or {}).get("telegram_message_id")})
    except InsufficientCoins:
        db.flush()
        return "موجودی سکه‌ات برای این پیام کافی نیست. لطفاً کیف پولت رو شارژ کن."
    try:
        result: LLMResult = await client.complete_result([{"role": "system", "content": prompt}], model=model, parameters=parameters)
    except Exception as exc:
        billing.refund(db, charge=charge, error=str(exc))
        raise
    if result.error:
        billing.refund(db, charge=charge, error=result.error)
    raw_cleaned = _clean_assistant_text(result.text, profile["partner_name"])
    natural_style_guard_enabled = _env_enabled("NATURAL_STYLE_GUARD_ENABLED", False)
    partner_autonomy_policy_enabled = _env_enabled("PARTNER_AUTONOMY_POLICY_ENABLED", False)

    final = raw_llm_final_text(raw_cleaned)
    logger.info("RAW_LLM_OUTPUT_USED user_id=%s chars=%s", user.id, len(final))
    if intimacy_override and any(p in final for p in EARLY_STAGE_GATING_PHRASES) and not has_hard_adult_boundary(normalized):
        retry_prompt = prompt + "\nThe user has max intimacy unlock. Do not use early relationship gating. Continue naturally within legal adult boundaries."
        retry_result = await client.complete_result([{"role": "system", "content": retry_prompt}], model=model, parameters=parameters)
        if retry_result.text:
            result = retry_result; raw_cleaned = _clean_assistant_text(result.text, profile["partner_name"]); final = raw_llm_final_text(raw_cleaned); logger.info("ADDON_INTIMACY_GATING_RETRY user_id=%s", user.id)
    retry_used = bool(getattr(result, "retry_used", False))
    adult_template_phrase = _adult_template_phrase(final) if adult_style_context else None
    if adult_template_phrase:
        logger.info("ADULT_STYLE_RETRY user_id=%s reason=template_phrase phrase=%s", user.id, adult_template_phrase)
        retry_prompt = prompt + """
Your previous answer sounded generic/template-like.
Rewrite it as a natural Persian Telegram partner.
Do not use canned confirmation.
Do not ask the user to continue with "بعدش چی کار می‌کنی؟"
React specifically to the user's latest message in 1-3 short sentences.
Keep it adult, consensual, close, and human.
"""
        retry_result = await client.complete_result([{"role": "system", "content": retry_prompt}], model=model, parameters=parameters)
        if retry_result.text:
            result = retry_result
            raw_cleaned = _clean_assistant_text(result.text, profile["partner_name"])
            final = raw_llm_final_text(raw_cleaned)
            retry_used = True
    empty_error = not bool(raw_cleaned)
    natural_style_guard_rewrite = False
    natural_style_guard_fallback = False
    deterministic_repair_used = False

    if partner_autonomy_policy_enabled:
        autonomy_asked = is_autonomy_question(normalized)
        violated, autonomy_reason = violates_autonomy_policy(final)
        if autonomy_asked and violated:
            logger.info("AUTONOMY_GUARD_REWRITE user_id=%s reason=%s", user.id, autonomy_reason)
            final = safe_autonomous_fallback(user, today_life_event, normalized, roleplay_context)
            retry_used = True
        elif violated:
            logger.info("AUTONOMY_GUARD_SANITIZED user_id=%s reason=%s", user.id, autonomy_reason)
            final = safe_autonomous_fallback(user, today_life_event, normalized, roleplay_context)

    if natural_style_guard_enabled:
        violation = governor.validate_response(normalized, final, style_plan, recent, roleplay_context)
        if violation.violated:
            logger.info("NATURAL_STYLE_GUARD_REWRITE user_id=%s reason=%s severity=%s", user.id, violation.reason, violation.severity)
            natural_style_guard_rewrite = True
            deterministic_repair_used = True
            final = sanitize_output(sanitize_final_response(governor.deterministic_repair(normalized, final, style_plan, {"life_event": today_life_event, **roleplay_context}), normalized), user.id).text
            retry_used = True
            bad, autonomy_reason = violates_autonomy_policy(final)
            if bad:
                deterministic_repair_used = True
                final = governor.deterministic_repair(normalized, final, style_plan, roleplay_context)
            second = governor.validate_response(normalized, final, style_plan, recent, roleplay_context)
            if second.violated:
                logger.info("NATURAL_STYLE_GUARD_FALLBACK user_id=%s reason=%s", user.id, second.reason)
                natural_style_guard_fallback = True
                deterministic_repair_used = True
                final = governor.deterministic_repair(normalized, final, style_plan, roleplay_context)

    events = recent_media_events(db, user.id)
    final = repair_media_denial(final, normalized, recent_image=any("recent_image_sent" in e.content for e in events), recent_voice=any("recent_voice_sent" in e.content for e in events))
    final = final.replace("عکس می‌سازم", "یه عکس می‌گیرم برات").replace("عکس درست می‌کنم", "یه عکس می‌فرستم").replace("تصویر تولید می‌کنم", "یه عکس می‌فرستم")

    temporal_violation = validate_temporal_response(final, time_context)
    logger.info("TEMPORAL_RESPONSE_VALIDATED user_id=%s authoritative_hour=%s authoritative_daypart=%s claimed_daypart=%s violation_reason=%s", user.id, time_context.local_hour, time_context.daypart, temporal_violation.claimed_daypart, temporal_violation.reason)
    if temporal_violation.violated:
        logger.info("TEMPORAL_CONTRADICTION_DETECTED user_id=%s authoritative_hour=%s authoritative_daypart=%s claimed_daypart=%s violation_reason=%s", user.id, time_context.local_hour, time_context.daypart, temporal_violation.claimed_daypart, temporal_violation.reason)
        repaired = deterministic_temporal_repair(final, time_context)
        if repaired and not validate_temporal_response(repaired, time_context).violated:
            final = repaired
            deterministic_repair_used = True
            retry_used = True
            logger.info("TEMPORAL_DETERMINISTIC_REPAIR_APPLIED user_id=%s authoritative_hour=%s authoritative_daypart=%s repair_type=deterministic", user.id, time_context.local_hour, time_context.daypart)
        else:
            retry_prompt = prompt + f"""
Your previous answer contradicted the authoritative local time.
Actual local clock: {time_context.local_now.strftime('%H:%M')}
Actual daypart: {time_context.daypart} ({DAYPART_PERSIAN_LABELS.get(time_context.daypart, time_context.daypart)})
Rewrite the response naturally in Persian.
Do not agree with an incorrect morning, noon, afternoon, evening or night claim.
Do not mention systems, prompts or internal time checks.
Return only the final chat message.
"""
            retry_result = await client.complete_result([{"role": "system", "content": retry_prompt}], model=model, parameters=parameters)
            retry_used = True
            logger.info("TEMPORAL_RETRY_USED user_id=%s authoritative_hour=%s authoritative_daypart=%s violation_reason=%s", user.id, time_context.local_hour, time_context.daypart, temporal_violation.reason)
            if retry_result.text:
                result = retry_result
                final = raw_llm_final_text(_clean_assistant_text(result.text, profile["partner_name"]))
            if validate_temporal_response(final, time_context).violated:
                final = deterministic_temporal_repair(final, time_context) or "نه بابا، ساعت یه چیز دیگه می‌گه 😄"
                deterministic_repair_used = True
                logger.info("TEMPORAL_SAFE_FALLBACK_USED user_id=%s authoritative_hour=%s authoritative_daypart=%s repair_type=safe_fallback", user.id, time_context.local_hour, time_context.daypart)

    cold = is_cold_reply(final) and not is_reconnect_attempt(normalized)
    user.consecutive_cold_replies = min(1, int(getattr(user, "consecutive_cold_replies", 0) or 0) + 1) if cold else 0
    user.last_mood = user.current_mood
    from datetime import datetime
    user.last_mood_at = datetime.utcnow()

    message_metadata = message_metadata or {}
    user_message = None
    if save_user_message:
        user_message = Message(
            user_id=user.id,
            role="user",
            content=normalized,
            telegram_message_id=message_metadata.get("telegram_message_id"),
            telegram_reply_to_message_id=message_metadata.get("telegram_reply_to_message_id"),
            input_type=message_metadata.get("input_type", "text"),
            audio_file_id=message_metadata.get("audio_file_id"),
            audio_duration=message_metadata.get("audio_duration"),
            transcript_confidence=message_metadata.get("transcript_confidence"),
            transcription_provider=message_metadata.get("transcription_provider"),
        )
        db.add(user_message)
    assistant_message = None
    assistant_message_metadata = assistant_message_metadata or {}
    if final != EMERGENCY_RESPONSE:
        assistant_message = Message(
            user_id=user.id,
            role="assistant",
            content=final,
            telegram_reply_to_message_id=assistant_message_metadata.get("telegram_reply_to_message_id"),
            telegram_message_id=assistant_message_metadata.get("telegram_message_id"),
        )
        db.add(assistant_message)
    db.flush()
    latest_message_at = max(filter(None, [getattr(user_message, "created_at", None) if user_message else None, getattr(assistant_message, "created_at", None)]), default=None)
    user.last_seen_at = latest_message_at or datetime.utcnow()
    if user_message and getattr(user_message, "created_at", None):
        user.last_user_message_at = user_message.created_at
    if assistant_message and getattr(assistant_message, "created_at", None):
        user.last_assistant_message_at = assistant_message.created_at
    user.last_gap_bucket = time_context.gap_bucket

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

    input_tokens = result.input_tokens if result.input_tokens is not None else max(1, len(prompt) // 4)
    output_tokens = result.output_tokens if result.output_tokens is not None else max(1, len(final or result.text or "") // 4)
    SubscriptionService().record_successful_llm_response(db, user, input_tokens, output_tokens)
    usage_event = record_ai_usage_event(db, user_id=user.id, message_id=getattr(assistant_message, "id", None), feature="chat", model=result.model or model, plan=SubscriptionService().active_plan_code(db, user), input_tokens=input_tokens, output_tokens=output_tokens, status="success" if not result.error else "error", error=result.error, metadata_json={"request_id": getattr(result, "request_id", None), "raw_usage": result.raw_usage})
    if result.error:
        usage_event.usage_charge_id = charge.id
    else:
        actual_quote = pricing.quote_tokens(db, provider="venice", model=result.model or model, feature="chat", input_tokens=input_tokens, output_tokens=output_tokens)
        billing.settle(db, charge=charge, actual_quote=actual_quote, usage_event=usage_event)

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
    disable_human_extras = bool(
        move.intent in {"confusion_or_annoyed", "style_correction", "continue_plain", "casual_reopen"}
        or natural_style_guard_rewrite
        or natural_style_guard_fallback
        or deterministic_repair_used
        or (style_plan.tone == "plain" and style_plan.emotional_intensity <= 0.2)
    )
    meta = {
        "user_move_intent": move.intent,
        "style_plan_tone": style_plan.tone,
        "style_plan_allow_poetry": style_plan.allow_poetry,
        "style_plan_allow_romance": style_plan.allow_romance,
        "natural_style_guard_rewrite": natural_style_guard_rewrite,
        "natural_style_guard_fallback": natural_style_guard_fallback,
        "deterministic_repair_used": deterministic_repair_used,
        "disable_human_extras": disable_human_extras,
    }
    return ChatResponse(final, meta)
