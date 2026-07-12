from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.image_generation import ImageGenerationJob, GeneratedVoiceOutput
from app.models.user import User
from app.models.billing import UsageCharge
from app.services.settings_service import SettingsService
from app.services.telegram_service import TelegramService

class GeneratedMediaArchiveService:
    def __init__(self, settings: SettingsService | None=None, telegram_service=None):
        self.settings=settings or SettingsService(); self.telegram_service=telegram_service
    def archive_chat_id(self, db: Session) -> int | None:
        raw=self.settings.get_str(db,'generated_media.chat_id','').strip()
        if not raw and self.settings.get_bool(db,'generated_media.fallback_to_support_media_chat_id',False): raw=get_settings().support_media_chat_id
        return int(raw) if str(raw).lstrip('-').isdigit() else None
    def enabled(self, db: Session, kind: str) -> bool:
        return self.settings.get_bool(db,'generated_media.forward_enabled',False) and self.settings.get_bool(db,f'generated_media.forward_{kind}s',True) and bool(self.archive_chat_id(db))
    async def archive_image(self, db: Session, job: ImageGenerationJob) -> bool:
        if not self.enabled(db,'image'):
            job.archive_status='disabled'; job.archive_error=None; db.flush(); return False
        chat_id=self.archive_chat_id(db); svc=self.telegram_service or TelegramService('chat')
        try:
            caption=self._image_caption(db, job)
            mid=await svc.copy_message(chat_id=chat_id, from_chat_id=job.chat_id, message_id=job.telegram_message_id, caption=caption)
            if not isinstance(mid,int) or mid<=0: raise RuntimeError('archive_missing_message_id')
            job.archive_status='sent'; job.archive_telegram_message_id=mid; job.archive_sent_at=datetime.utcnow(); job.archive_error=None; db.flush(); return True
        except Exception as exc:
            job.archive_status='failed'; job.archive_error=str(exc)[:500]; db.flush(); return False
    async def archive_voice(self, db: Session, voice: GeneratedVoiceOutput) -> bool:
        if not self.enabled(db,'voice'):
            voice.archive_status='disabled'; voice.archive_error=None; db.flush(); return False
        chat_id=self.archive_chat_id(db); svc=self.telegram_service or TelegramService('chat')
        try:
            mid=await svc.copy_message(chat_id=chat_id, from_chat_id=voice.chat_id, message_id=voice.user_telegram_message_id, caption=self._voice_caption(db, voice))
            if not isinstance(mid,int) or mid<=0: raise RuntimeError('archive_missing_message_id')
            voice.archive_status='sent'; voice.archive_telegram_message_id=mid; voice.archive_sent_at=datetime.utcnow(); voice.archive_error=None; db.flush(); return True
        except Exception as exc:
            voice.archive_status='failed'; voice.archive_error=str(exc)[:500]; db.flush(); return False
    async def retry_archive(self, db: Session, media):
        media.archive_status='retrying'; db.flush()
        return await (self.archive_image(db, media) if isinstance(media, ImageGenerationJob) else self.archive_voice(db, media))
    def _charge_line(self, db, usage_charge_id):
        c = db.get(UsageCharge, usage_charge_id) if usage_charge_id else None
        return f"Reserved/charged/refunded coins: {getattr(c,'reserved_coins','—')}/{getattr(c,'charged_coins','—')}/{getattr(c,'refunded_coins','—')}"
    def _image_caption(self, db, job):
        user=db.get(User, job.user_id); prompt=(job.prompt or '')[:700]; meta=job.metadata_json or {}
        return f"Generated image #{job.id}\nUser ID: {job.user_id}\nTelegram ID: {getattr(user,'telegram_id',None)}\nJob/output ID: {job.id}\nUsage charge ID: {job.usage_charge_id}\nProvider/model: {job.provider}/{job.model}\n{self._charge_line(db, job.usage_charge_id)}\nSource message: {job.source_telegram_message_id}\nUser delivery message: {job.telegram_message_id}\nRequest: {(job.user_request or '')[:300]}\nContent mode: {job.content_mode}\nLocal datetime/timezone: {meta.get('local_datetime','—')} {meta.get('timezone','')}\nPrompt: {prompt}"
    def _voice_caption(self, db, v):
        user=db.get(User, v.user_id); meta=v.metadata_json or {}
        return f"Generated voice #{v.id}\nUser ID: {v.user_id}\nTelegram ID: {getattr(user,'telegram_id',None)}\nVoice output ID: {v.id}\nUsage charge ID: {v.usage_charge_id}\nProvider/model: {v.provider}/{v.model}\nVoice: {v.voice_name}\n{self._charge_line(db, v.usage_charge_id)}\nSource message: {v.source_telegram_message_id}\nUser delivery message: {v.user_telegram_message_id}\nMIME/bytes/checksum: {v.mime_type}/{v.byte_size}/{v.checksum}\nCharged coins: {meta.get('charged_coins','—')}\nText: {(v.text_spoken or '')[:500]}"
