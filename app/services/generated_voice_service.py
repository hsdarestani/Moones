from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.image_generation import GeneratedVoiceOutput
from app.services.generated_media_archive_service import GeneratedMediaArchiveService
from app.services.telegram_service import TelegramDeliveryResult
from app.services.media_continuity_service import record_media_delivery
from app.services.interaction_reliability import aggregate_voice_feedback, VOICE_DIMENSIONS

logger = logging.getLogger(__name__)

VOICE_FEEDBACK_PARSER_VERSION = "fa-rules-v1"


def parse_voice_feedback(text: str) -> tuple[dict[str, float], float]:
    """Parse explicit Persian voice-style corrections; never retain the raw text here."""
    normalized = (text or "").replace("‌", " ").strip().lower()
    rules = (
        (("خیلی تنده", "آروم تر حرف بزن", "آهسته تر"), {"pace": -.8, "softness": .5}),
        (("سریع تر حرف بزن",), {"pace": .8, "energy": .3}),
        (("گرم تر",), {"warmth": .8}), (("بچه گونه",), {"perceived_age": .7}),
        (("بم تر",), {"pitch": -.8}), (("زیرتر",), {"pitch": .8}),
        (("واضح تر",), {"clarity": .8}), (("رسمی نباش",), {"formality": -.8}),
        (("بازیگوش تر",), {"playfulness": .8, "energy": .3}),
        (("جدی تر",), {"playfulness": -.7, "formality": .3}),
        (("این صدا خوبه", "این صدا رو دوست دارم"), {"warmth": .4, "softness": .3}),
        (("این صدا رو دوست ندارم", "این صدا خوب نیست"), {"warmth": -.4}),
    )
    dimensions: dict[str, float] = {}
    for phrases, values in rules:
        if any(phrase in normalized for phrase in phrases): dimensions.update(values)
    return ({key: max(-1.0, min(1.0, value)) for key, value in dimensions.items()
             if key in VOICE_DIMENSIONS}, .9 if dimensions else 0.0)


def capture_voice_feedback(db: Session, *, user_id: int, text: str,
                           source_message_id: int, reply_to_message_id: int | None,
                           now: datetime | None = None) -> dict | None:
    now = now or datetime.utcnow()
    query = select(GeneratedVoiceOutput).where(
        GeneratedVoiceOutput.user_id == user_id,
        GeneratedVoiceOutput.status == "sent",
        GeneratedVoiceOutput.sent_at >= now - timedelta(minutes=20),
    ).order_by(GeneratedVoiceOutput.sent_at.desc(), GeneratedVoiceOutput.id.desc())
    if reply_to_message_id:
        query = query.where(GeneratedVoiceOutput.user_telegram_message_id == reply_to_message_id)
    elif not any(word in (text or "") for word in ("صدا", "صدات", "وویس", "ویس")):
        logger.info("VOICE_FEEDBACK_IGNORED user_id=%s reason=not_voice_referential", user_id)
        return None
    voice = db.scalar(query.limit(1))
    dimensions, confidence = parse_voice_feedback(text)
    if not voice or not dimensions:
        logger.info("VOICE_FEEDBACK_IGNORED user_id=%s reason=%s", user_id,
                    "voice_not_found" if not voice else "no_supported_dimension")
        return None
    metadata = dict(voice.metadata_json or {})
    events = list(metadata.get("voice_feedback_events") or [])
    if any(event.get("source_message_id") == source_message_id for event in events):
        logger.info("VOICE_FEEDBACK_IGNORED user_id=%s reason=duplicate", user_id)
        return None
    event = {"user_id": user_id, "generated_voice_output_id": voice.id,
             "generated_telegram_message_id": voice.user_telegram_message_id,
             "source_message_id": source_message_id, "dimensions": dimensions,
             "confidence": confidence, "created_at": now.isoformat(),
             "parser_version": VOICE_FEEDBACK_PARSER_VERSION}
    events.append(event); metadata["voice_feedback_events"] = events[-15:]
    voice.metadata_json = metadata; db.flush()
    logger.info("VOICE_FEEDBACK_CAPTURED user_id=%s dimensions=%s", user_id, sorted(dimensions))
    return event


def load_voice_feedback_profile(db: Session, *, user_id: int) -> dict[str, float]:
    rows = db.scalars(select(GeneratedVoiceOutput).where(
        GeneratedVoiceOutput.user_id == user_id,
        GeneratedVoiceOutput.metadata_json.is_not(None),
    ).order_by(GeneratedVoiceOutput.created_at.desc()).limit(15)).all()
    events = []
    for row in reversed(rows):
        for event in (row.metadata_json or {}).get("voice_feedback_events", []):
            if event.get("user_id") == user_id and event.get("parser_version"):
                events.append(event)
    events = sorted(events, key=lambda e: e.get("created_at", ""))[-15:]
    profile = aggregate_voice_feedback(events)
    logger.info("VOICE_FEEDBACK_PROFILE_LOADED user_id=%s count=%s dimensions=%s",
                user_id, len(events), sorted(k for k, v in profile.items() if v))
    return profile


def voice_feedback_markup(voice_id: int) -> dict:
    return {'inline_keyboard': [[{'text': '👍 خوب بود', 'callback_data': f'voicefb:{voice_id}:positive'}, {'text': '👎 خوب نبود', 'callback_data': f'voicefb:{voice_id}:negative'}]]}


async def persist_and_deliver_voice(db: Session, *, user, chat_id: int, source_telegram_message_id: int | None, text: str, audio_bytes: bytes, voice_name: str | None, provider: str, model: str, usage_charge, telegram_service) -> GeneratedVoiceOutput:
    idem = f'tg:voice:{getattr(user,"telegram_id",user.id)}:{source_telegram_message_id}:{hashlib.sha256((text or "").encode()).hexdigest()[:16]}'
    existing = db.scalar(select(GeneratedVoiceOutput).where(GeneratedVoiceOutput.idempotency_key == idem))
    if existing and existing.status == 'sent' and existing.user_telegram_message_id:
        return existing
    checksum = hashlib.sha256(audio_bytes).hexdigest()
    charged = int(getattr(usage_charge, 'charged_coins', 0) or getattr(usage_charge, 'reserved_coins', 0) or 0)
    voice = existing or GeneratedVoiceOutput(idempotency_key=idem, user_id=user.id, chat_id=chat_id, source_telegram_message_id=source_telegram_message_id)
    if not existing:
        db.add(voice)
    voice.usage_charge_id = getattr(usage_charge, 'id', None)
    voice.text_spoken = text
    voice.voice_name = voice_name
    voice.provider = provider
    voice.model = model
    voice.mime_type = 'audio/ogg'
    voice.byte_size = len(audio_bytes or b'')
    voice.checksum = checksum
    voice.audio_bytes = audio_bytes
    voice.generated_at = voice.generated_at or datetime.utcnow()
    voice.metadata_json = {**(voice.metadata_json or {}), 'charged_coins': charged}
    db.flush()
    if voice.status == 'sent' and voice.user_telegram_message_id:
        return voice
    voice.attempt_count += 1
    try:
        delivery = await telegram_service.send_voice(chat_id, audio_bytes, None, reply_markup=voice_feedback_markup(voice.id))
        mid = getattr(delivery, 'message_id', delivery)
        if not isinstance(mid, int) or mid <= 0:
            raise RuntimeError('telegram_voice_missing_message_id')
        voice.user_telegram_message_id = mid
        voice.status = 'sent'
        voice.sent_at = datetime.utcnow()
        voice.error_code = None
        voice.error_message = None
        await GeneratedMediaArchiveService().archive_voice(db, voice)
        record_media_delivery(db, user_id=user.id, media_type='voice', request_summary=text or '', generated_summary=f'voice_name={voice_name or ""}; model={model}', telegram_message_id=mid)
    except Exception as exc:
        voice.status = 'delivery_failed'
        voice.error_code = 'telegram_delivery'
        voice.error_message = str(exc)[:500]
        db.flush()
        raise
    db.flush()
    return voice


def store_voice_feedback(db: Session, *, user_id: int, voice_id: int, rating: str) -> GeneratedVoiceOutput | None:
    voice = db.get(GeneratedVoiceOutput, voice_id)
    if not voice or voice.user_id != user_id:
        return None
    voice.feedback = rating
    db.flush()
    return voice
