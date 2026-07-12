from __future__ import annotations
import hashlib
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.image_generation import GeneratedVoiceOutput
from app.services.generated_media_archive_service import GeneratedMediaArchiveService
from app.services.telegram_service import TelegramDeliveryResult


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
