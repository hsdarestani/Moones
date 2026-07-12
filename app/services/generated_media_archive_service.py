from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.models.image_generation import ImageGenerationJob, GeneratedVoiceOutput
from app.models.user import User
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
        if not self.enabled(db,'image'): return False
        chat_id=self.archive_chat_id(db); svc=self.telegram_service or TelegramService('chat')
        try:
            caption=self._image_caption(db, job)
            mid=await svc.copy_message(chat_id=chat_id, from_chat_id=job.chat_id, message_id=job.telegram_message_id, caption=caption)
            if not isinstance(mid,int) or mid<=0: raise RuntimeError('archive_missing_message_id')
            job.archive_status='sent'; job.archive_telegram_message_id=mid; job.archive_sent_at=datetime.utcnow(); job.archive_error=None; db.flush(); return True
        except Exception as exc:
            job.archive_status='failed'; job.archive_error=str(exc)[:500]; db.flush(); return False
    async def archive_voice(self, db: Session, voice: GeneratedVoiceOutput) -> bool:
        if not self.enabled(db,'voice'): return False
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
    def _image_caption(self, db, job):
        user=db.get(User, job.user_id); prompt=(job.prompt or '')[:700]
        return f"Generated image #{job.id}\nUser ID: {job.user_id}\nTelegram ID: {getattr(user,'telegram_id',None)}\nJob ID: {job.id}\nUsage charge ID: {job.usage_charge_id}\nProvider/model: {job.provider}/{job.model}\nCharged coins: {(job.metadata_json or {}).get('charged_coins','—')}\nSource message: {job.source_telegram_message_id}\nUser delivery message: {job.telegram_message_id}\nRequest: {(job.user_request or '')[:300]}\nMode: {job.content_mode}\nLocal time: {(job.metadata_json or {}).get('local_datetime','—')} {(job.metadata_json or {}).get('timezone','')}\nPrompt: {prompt}"
    def _voice_caption(self, db, v):
        user=db.get(User, v.user_id)
        return f"Generated voice #{v.id}\nUser ID: {v.user_id}\nTelegram ID: {getattr(user,'telegram_id',None)}\nUsage charge ID: {v.usage_charge_id}\nProvider/model: {v.provider}/{v.model}\nSource message: {v.source_telegram_message_id}\nUser delivery message: {v.user_telegram_message_id}\nText: {(v.text_spoken or '')[:500]}"
