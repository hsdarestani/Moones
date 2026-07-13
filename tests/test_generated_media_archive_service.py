import asyncio
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.image_generation import GeneratedVoiceOutput, ImageGenerationArtifact, ImageGenerationJob
from app.models.settings import AppSetting
from app.models.user import User
from app.services.generated_media_archive_service import GENERATED_MEDIA_CAPTION_MAX, GeneratedMediaArchiveService


class FakeTelegram:
    def __init__(self, *, copy_error=None):
        self.copy_error = copy_error
        self.copied = []
        self.photos = []
        self.voices = []

    async def copy_message(self, **kwargs):
        self.copied.append(kwargs)
        if self.copy_error:
            raise self.copy_error
        return 111

    async def send_photo_bytes(self, chat_id, photo_bytes, filename="photo.jpg", mime_type="image/jpeg", caption=None, reply_markup=None):
        self.photos.append({"chat_id": chat_id, "photo_bytes": photo_bytes, "caption": caption})
        return 222

    async def send_voice(self, chat_id, ogg_bytes, caption=None, reply_markup=None):
        self.voices.append({"chat_id": chat_id, "ogg_bytes": ogg_bytes, "caption": caption})
        return 333


def session():
    e = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(e, tables=[User.__table__, AppSetting.__table__, ImageGenerationJob.__table__, ImageGenerationArtifact.__table__, GeneratedVoiceOutput.__table__])
    s = sessionmaker(bind=e)()
    s.add(AppSetting(key="generated_media.forward_enabled", value="true", value_type="boolean"))
    s.add(AppSetting(key="generated_media.chat_id", value="-100", value_type="telegram_chat_id"))
    s.commit()
    return s


def make_job(s, *, request="make image", prompt="secret prompt"):
    u = User(telegram_id=987654321)
    s.add(u); s.flush()
    j = ImageGenerationJob(idempotency_key="k", correlation_id="c", user_id=u.id, chat_id=10, telegram_message_id=20, source_telegram_message_id=19, status="sent", user_request=request, prompt=prompt, negative_prompt="secret negative", metadata_json={"scene_summary": "short scene"})
    s.add(j); s.flush()
    s.add(ImageGenerationArtifact(job_id=j.id, mime_type="image/jpeg", checksum="abc", byte_size=3, image_bytes=b"img"))
    s.commit()
    return j


def test_very_long_prompt_produces_caption_below_configured_maximum():
    s = session(); job = make_job(s, request="سلام " * 1000, prompt="FULL_PROMPT " * 1000)
    caption = GeneratedMediaArchiveService()._image_caption(s, job)
    assert len(caption) <= GENERATED_MEDIA_CAPTION_MAX
    assert "FULL_PROMPT" not in caption
    assert "secret negative" not in caption


def test_unicode_persian_truncation_is_safe():
    s = session(); job = make_job(s, request="درخواست خیلی طولانی با ایموجی 🤍 " * 300)
    caption = GeneratedMediaArchiveService()._image_caption(s, job)
    assert len(caption) <= GENERATED_MEDIA_CAPTION_MAX
    assert "�" not in caption
    assert "Telegram ID: 987654321" in caption


def test_media_forwarding_succeeds_with_compact_caption():
    async def run():
        s = session(); job = make_job(s, request="x" * 5000)
        tg = FakeTelegram()
        ok = await GeneratedMediaArchiveService(telegram_service=tg).archive_image(s, job)
        assert ok is True
        assert len(tg.copied[0]["caption"]) <= GENERATED_MEDIA_CAPTION_MAX
        assert job.archive_status == "sent"
    asyncio.run(run())


def test_copy_caption_length_failure_retries_direct_send_with_compact_caption():
    async def run():
        s = session(); job = make_job(s, request="x" * 5000)
        tg = FakeTelegram(copy_error=RuntimeError("Bad Request: message caption is too long"))
        ok = await GeneratedMediaArchiveService(telegram_service=tg).archive_image(s, job)
        assert ok is True
        assert tg.photos
        assert len(tg.photos[0]["caption"]) <= GENERATED_MEDIA_CAPTION_MAX
    asyncio.run(run())


def test_long_diagnostics_are_omitted_and_archive_failure_does_not_affect_delivery_status():
    async def run():
        s = session(); job = make_job(s, request="ok", prompt="TRACEBACK provider response metadata json " * 200)
        tg = FakeTelegram(copy_error=RuntimeError("network token=123:ABC failed"))
        ok = await GeneratedMediaArchiveService(telegram_service=tg).archive_image(s, job)
        assert ok is False
        assert job.status == "sent"
        assert job.archive_status == "failed"
        assert "TRACEBACK" not in tg.copied[0]["caption"]
    asyncio.run(run())


def test_no_secret_token_appears_in_generated_media_logs(caplog):
    async def run():
        s = session(); job = make_job(s)
        tg = FakeTelegram(copy_error=RuntimeError("bot123456:SECRET Bad Request"))
        with caplog.at_level(logging.WARNING):
            await GeneratedMediaArchiveService(telegram_service=tg).archive_image(s, job)
        assert "bot123456:SECRET" not in caplog.text
        assert "GENERATED_MEDIA_FORWARD_FAILED" in caplog.text
    asyncio.run(run())
