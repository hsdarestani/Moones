"""Small, deterministic interaction controls shared by Telegram entry points.

The objects in this module deliberately contain no provider prompt text or secrets.  They
turn Telegram metadata and an explicit user instruction into bounded, testable plans.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import re
from typing import Any, Iterable

from sqlalchemy import select

from app.models.message import Message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplyContext:
    telegram_message_id: int | None
    message_type: str = "unknown"
    author_role: str = "external"
    text: str | None = None
    internal_message_id: int | None = None
    image_job_id: int | None = None
    generated_voice_output_id: int | None = None
    resolved: bool = False

    def prompt_block(self) -> str:
        if not self.resolved:
            return "[Reply context]\nThe replied-to message is unavailable. Do not guess its contents."
        safe = re.sub(r"[\x00-\x1f]+", " ", self.text or "").strip()[:600]
        # Internal/artifact IDs are intentionally excluded.
        return ("[Reply context]\n"
                f"The user is replying to a {self.author_role} {self.message_type} message.\n"
                f"Replied-to content: {safe or '(media without text)'}")


def _payload_media_type(message: Any) -> str:
    for attr, kind in (("photo", "image"), ("voice", "voice"), ("audio", "voice"),
                       ("sticker", "sticker"), ("document", "document")):
        if getattr(message, attr, None):
            return kind
    return "text" if (getattr(message, "text", None) or getattr(message, "caption", None)) else "unknown"


def resolve_reply_context(db, *, user_id: int, chat_id: int, reply_message: Any | None) -> ReplyContext | None:
    """Resolve only a message owned by ``user_id``; never trust payload text as ownership proof."""
    if reply_message is None:
        return None
    telegram_id = getattr(reply_message, "message_id", None)
    row = db.scalar(select(Message).where(Message.user_id == user_id,
                                          Message.telegram_message_id == telegram_id)
                    .order_by(Message.id.desc()).limit(1)) if telegram_id is not None else None
    payload_role = "assistant" if bool(getattr(getattr(reply_message, "from_user", None), "is_bot", False)) else "external"
    if row is None:
        context = ReplyContext(telegram_id, _payload_media_type(reply_message), payload_role,
                               getattr(reply_message, "text", None) or getattr(reply_message, "caption", None))
    else:
        metadata = row.metadata_json or {}
        context = ReplyContext(telegram_id, row.input_type or _payload_media_type(reply_message), row.role,
                               row.content or getattr(reply_message, "caption", None), row.id,
                               metadata.get("image_job_id"), metadata.get("generated_voice_output_id"), True)
    logger.info("REPLY_CONTEXT_RESOLVED user_id=%s chat_id=%s resolved=%s type=%s role=%s",
                user_id, chat_id, context.resolved, context.message_type, context.author_role)
    return context


class RequestedLength(StrEnum):
    VERY_SHORT = "very_short"; SHORT = "short"; NORMAL = "normal"
    DETAILED = "detailed"; VERY_DETAILED = "very_detailed"


@dataclass(frozen=True)
class ResponseStylePlan:
    requested_length: str = RequestedLength.NORMAL
    answer_mode: str = "direct_answer"
    question_budget: str = "one"
    emotional_tone: str = "natural"
    formatting: str | None = None

    @property
    def max_tokens(self) -> int:
        return {"very_short": 90, "short": 180, "normal": 350, "detailed": 700,
                "very_detailed": 1100}[self.requested_length]

    def prompt_block(self) -> str:
        return ("[Explicit response style]\n"
                f"length={self.requested_length}; mode={self.answer_mode}; "
                f"question_budget={self.question_budget}; formatting={self.formatting or 'natural'}.\n"
                "Follow this explicit request before relationship-stage defaults; answer the request first.")


def resolve_response_style(text: str) -> ResponseStylePlan:
    t = re.sub(r"\s+", " ", (text or "").lower())
    length = "normal"
    if re.search(r"کامل.*جزئیات|با جزئیات|خیلی مفصل|مفصل.*کامل", t): length = "very_detailed"
    elif re.search(r"مفصل|کامل توضیح|قدم به قدم|گام به گام", t): length = "detailed"
    elif re.search(r"فقط جواب|کوتاه|خلاصه", t): length = "very_short" if "کوتاه" in t or "فقط" in t else "short"
    mode = "direct_answer"
    if re.search(r"قدم به قدم|گام به گام", t): mode = "step_by_step"
    elif re.search(r"فقط گوش کن", t): mode = "listening"
    elif re.search(r"نظر.*بگو|راهکار بده", t): mode = "advice"
    elif re.search(r"توضیح", t): mode = "explanation"
    questions = "zero" if re.search(r"سوال نپرس|زیاد سوال نپرس|فقط جواب|فقط گوش", t) else "one"
    formatting = "numbered_steps" if mode == "step_by_step" else None
    plan = ResponseStylePlan(length, mode, questions, "natural", formatting)
    logger.info("RESPONSE_STYLE_RESOLVED requested_length=%s answer_mode=%s question_budget=%s",
                length, mode, questions)
    return plan


_IMAGE_PROMISE = re.compile(r"(?:اینم\s*(?:عکس|تصویر)|(?:عکس|تصویر).{0,20}(?:فرستادم|می[‌ ]?فرستم|می[‌ ]?گیرم)|الان.{0,12}(?:می[‌ ]?فرستم|می[‌ ]?گیرم))")


def block_unbacked_image_promise(text: str, *, image_action_succeeded: bool = False) -> tuple[str, bool]:
    if image_action_succeeded or not _IMAGE_PROMISE.search(text or ""):
        return text, False
    logger.info("IMAGE_PROMISE_BLOCKED reason=normal_chat_without_artifact_or_job")
    return _IMAGE_PROMISE.sub("برای فرستادن عکس باید درخواست عکس با موفقیت ثبت بشه", text), True


@dataclass(frozen=True)
class StickerInterpretation:
    intent_category: str
    emotion_category: str
    confidence: float
    semantic_hint: str
    text_response_appropriate: bool = True
    sticker_response_appropriate: bool = False


def interpret_sticker(*, emoji: str | None, set_name: str | None = None,
                      preceding_text: str | None = None, replying_to_sticker: bool = False) -> StickerInterpretation:
    e = emoji or ""; context = (preceding_text or "").lower()
    groups = [(("😂", "🤣", "😆"), "reaction", "amusement", "داره می‌خنده"),
              (("😢", "😭", "☹"), "emotion", "sadness", "ناراحتی یا همدلی"),
              (("❤️", "❤", "😘", "🥰", "😍"), "affection", "affection", "ابراز محبت"),
              (("😡", "🤬", "😠"), "emotion", "anger", "ناراحتی یا عصبانیت"),
              (("👍", "👌", "✅"), "approval", "positive", "تأیید"),
              (("👋",), "greeting", "friendly", "سلام یا خداحافظی"),
              (("😳", "🙈"), "reaction", "embarrassment", "خجالت یا شیطنت"))]
    for emojis, intent, emotion, hint in groups:
        if any(x in e for x in emojis):
            result = StickerInterpretation(intent, emotion, .9, hint, True, not replying_to_sticker)
            break
    else:
        hint = "واکنش مبهم به پیام قبلی" if context else "استیکر با معنی نامطمئن"
        result = StickerInterpretation("contextual_reaction", "unknown", .3, hint, True, False)
    logger.info("STICKER_INTERPRETED intent=%s emotion=%s confidence=%.2f", result.intent_category, result.emotion_category, result.confidence)
    return result


VOICE_DIMENSIONS = ("pitch", "pace", "warmth", "energy", "formality", "playfulness", "softness", "clarity", "perceived_age")


def aggregate_voice_feedback(events: Iterable[dict], *, maximum_events: int = 15) -> dict[str, float]:
    """Return a compact neutral-centred profile; raw comments are never returned."""
    totals = {key: 0.0 for key in VOICE_DIMENSIONS}; weights = {key: 0.0 for key in VOICE_DIMENSIONS}
    deduped: dict[tuple, dict] = {}
    for event in list(events)[-maximum_events:]:
        dimensions = event.get("dimensions") or {}
        signature = (event.get("source_message_id"), tuple(sorted(dimensions.items())))
        deduped[signature] = event
    ordered = list(deduped.values())
    for index, event in enumerate(ordered):
        recency = .55 + .45 * ((index + 1) / max(1, len(ordered)))
        confidence = max(0.0, min(1.0, float(event.get("confidence", .7))))
        for key, raw in (event.get("dimensions") or {}).items():
            if key not in totals: continue
            value = max(-1.0, min(1.0, float(raw))); weight = recency * confidence
            totals[key] += value * weight; weights[key] += weight
    # Neutral prior of 1.5 prevents one comment from redefining the voice.
    profile = {key: round(max(-.75, min(.75, totals[key] / (weights[key] + 1.5))), 3) for key in totals}
    logger.info("VOICE_FEEDBACK_AGGREGATED recent_count=%s effective_count=%s dimensions=%s",
                len(list(events)) if isinstance(events, list) else len(ordered), len(ordered), len(profile))
    return profile
