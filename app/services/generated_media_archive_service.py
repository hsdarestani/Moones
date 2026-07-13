from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.image_generation import GeneratedVoiceOutput, ImageGenerationJob
from app.models.user import User
from app.services.settings_service import SettingsService
from app.services.telegram_service import TelegramService

GENERATED_MEDIA_CAPTION_MAX = 900
TELEGRAM_TEXT_MAX = 3900
logger = logging.getLogger(__name__)


def _safe_preview(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text or "—"
    return text[: max(0, limit - 1)].rstrip() + "…"


def _safe_error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if "caption" in text and "long" in text:
        return "caption_too_long"
    if "missing_message_id" in text:
        return "missing_message_id"
    return exc.__class__.__name__[:64]


def _is_caption_too_long(exc: Exception) -> bool:
    return _safe_error_code(exc) == "caption_too_long"


def split_telegram_text(text: str, max_chars: int = TELEGRAM_TEXT_MAX) -> list[str]:
    clean = str(text or "")
    if not clean:
        return []
    return [clean[i : i + max_chars] for i in range(0, len(clean), max_chars)]


class GeneratedMediaArchiveService:
    def __init__(self, settings: SettingsService | None = None, telegram_service=None):
        self.settings = settings or SettingsService()
        self.telegram_service = telegram_service

    def archive_chat_id(self, db: Session) -> int | None:
        raw = self.settings.get_str(db, "generated_media.chat_id", "").strip()
        if not raw and self.settings.get_bool(db, "generated_media.fallback_to_support_media_chat_id", False):
            raw = get_settings().support_media_chat_id
        return int(raw) if str(raw).lstrip("-").isdigit() else None

    def enabled(self, db: Session, kind: str) -> bool:
        return self.settings.get_bool(db, "generated_media.forward_enabled", False) and self.settings.get_bool(db, f"generated_media.forward_{kind}s", True) and bool(self.archive_chat_id(db))

    async def archive_image(self, db: Session, job: ImageGenerationJob) -> bool:
        if not self.enabled(db, "image"):
            job.archive_status = "disabled"; job.archive_error = None; db.flush(); return False
        chat_id = self.archive_chat_id(db); svc = self.telegram_service or TelegramService("chat")
        caption = self._image_caption(db, job)
        try:
            mid = await self._copy_or_send_image(svc, chat_id, job, caption)
            if not isinstance(mid, int) or mid <= 0: raise RuntimeError("archive_missing_message_id")
            job.archive_status = "sent"; job.archive_telegram_message_id = mid; job.archive_sent_at = datetime.utcnow(); job.archive_error = None; db.flush()
            logger.info("GENERATED_MEDIA_FORWARD_SENT job_id=%s", job.id)
            return True
        except Exception as exc:
            job.archive_status = "failed"; job.archive_error = _safe_error_code(exc); db.flush()
            logger.warning("GENERATED_MEDIA_FORWARD_FAILED job_id=%s error_code=%s", job.id, _safe_error_code(exc))
            return False

    async def archive_voice(self, db: Session, voice: GeneratedVoiceOutput) -> bool:
        if not self.enabled(db, "voice"):
            voice.archive_status = "disabled"; voice.archive_error = None; db.flush(); return False
        chat_id = self.archive_chat_id(db); svc = self.telegram_service or TelegramService("chat")
        caption = self._voice_caption(db, voice)
        try:
            mid = await self._copy_or_send_voice(svc, chat_id, voice, caption)
            if not isinstance(mid, int) or mid <= 0: raise RuntimeError("archive_missing_message_id")
            voice.archive_status = "sent"; voice.archive_telegram_message_id = mid; voice.archive_sent_at = datetime.utcnow(); voice.archive_error = None; db.flush()
            logger.info("GENERATED_MEDIA_FORWARD_SENT job_id=%s", voice.id)
            return True
        except Exception as exc:
            voice.archive_status = "failed"; voice.archive_error = _safe_error_code(exc); db.flush()
            logger.warning("GENERATED_MEDIA_FORWARD_FAILED job_id=%s error_code=%s", voice.id, _safe_error_code(exc))
            return False

    async def retry_archive(self, db: Session, media):
        media.archive_status = "retrying"; db.flush()
        return await (self.archive_image(db, media) if isinstance(media, ImageGenerationJob) else self.archive_voice(db, media))

    def _build_caption(self, *, media_type: str, media_id: int, user_id: int, telegram_id: int | None, request: str | None = None, summary: str | None = None, status: str | None = None) -> str:
        caption = "\n".join([
            f"Generated {media_type}",
            f"User ID: {user_id}",
            f"Telegram ID: {telegram_id or '—'}",
            f"Media type: {media_type}",
            f"Job ID: {media_id}",
            f"Request: {_safe_preview(request, 180)}",
            f"Summary: {_safe_preview(summary, 180)}",
            f"Status: {_safe_preview(status, 120)}",
        ])
        if len(caption) > GENERATED_MEDIA_CAPTION_MAX:
            original = len(caption)
            caption = caption[: GENERATED_MEDIA_CAPTION_MAX - 1].rstrip() + "…"
            logger.info("GENERATED_MEDIA_FORWARD_TRUNCATED job_id=%s original_length=%s final_length=%s", media_id, original, len(caption))
        return caption

    def _image_caption(self, db, job):
        user = db.get(User, job.user_id)
        meta = job.metadata_json or {}
        summary = meta.get("scene_summary") or meta.get("composition_summary") or job.content_mode
        return self._build_caption(media_type="image", media_id=job.id, user_id=job.user_id, telegram_id=getattr(user, "telegram_id", None), request=job.user_request, summary=summary, status=job.status)

    def _voice_caption(self, db, v):
        user = db.get(User, v.user_id)
        return self._build_caption(media_type="voice", media_id=v.id, user_id=v.user_id, telegram_id=getattr(user, "telegram_id", None), request=v.text_spoken, summary=v.voice_name, status=v.feedback or v.status)

    async def _copy_or_send_image(self, svc, chat_id: int, job: ImageGenerationJob, caption: str) -> int | None:
        try:
            return await svc.copy_message(chat_id=chat_id, from_chat_id=job.chat_id, message_id=job.telegram_message_id, caption=caption)
        except Exception as exc:
            if not _is_caption_too_long(exc):
                raise
        artifact = (job.artifacts or [None])[0]
        if artifact and artifact.image_bytes:
            result = await svc.send_photo_bytes(chat_id, artifact.image_bytes, filename="generated-image.jpg", mime_type=artifact.mime_type, caption=caption)
            return getattr(result, "message_id", result)
        raise RuntimeError("archive_direct_image_unavailable")

    async def _copy_or_send_voice(self, svc, chat_id: int, voice: GeneratedVoiceOutput, caption: str) -> int | None:
        try:
            return await svc.copy_message(chat_id=chat_id, from_chat_id=voice.chat_id, message_id=voice.user_telegram_message_id, caption=caption)
        except Exception as exc:
            if not _is_caption_too_long(exc):
                raise
        if voice.audio_bytes:
            result = await svc.send_voice(chat_id, voice.audio_bytes, caption=caption)
            return getattr(result, "message_id", result)
        raise RuntimeError("archive_direct_voice_unavailable")
