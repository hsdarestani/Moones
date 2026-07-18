from datetime import datetime, timedelta
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction
from app.models.addon import AddonProduct, UserAddon
from app.models.billing import UsageCharge
from app.models.usage import AiUsageEvent
from app.models.image_generation import PartnerVisualProfile, ImageGenerationJob, ImageGenerationArtifact, ImageGenerationFeedback
from app.services.image_generation_service import claim_next_job, cleanup_stale_artifacts

def session():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AddonProduct.__table__, UserAddon.__table__, UsageCharge.__table__, AiUsageEvent.__table__, PartnerVisualProfile.__table__, ImageGenerationJob.__table__, ImageGenerationArtifact.__table__, ImageGenerationFeedback.__table__])
    return sessionmaker(bind=e)()

def test_claim_sets_lock_and_expired_lock_recovery():
    s=session(); u=User(telegram_id=1); s.add(u); s.flush()
    j=ImageGenerationJob(idempotency_key='k', correlation_id='c', user_id=u.id, chat_id=1, scheduled_at=datetime.utcnow())
    s.add(j); s.commit()
    claimed=claim_next_job(s); assert claimed.id == j.id and claimed.status == 'processing' and claimed.lock_expires_at
    assert claim_next_job(s) is None
    claimed.status='queued'; claimed.lock_expires_at=datetime.utcnow()-timedelta(seconds=1); s.commit()
    assert claim_next_job(s).id == j.id

def test_stale_artifact_cleanup_clears_bytes():
    s=session(); u=User(telegram_id=1); s.add(u); s.flush(); j=ImageGenerationJob(idempotency_key='k', correlation_id='c', user_id=u.id, chat_id=1); s.add(j); s.flush()
    a=ImageGenerationArtifact(job_id=j.id,mime_type='image/png',checksum='x',byte_size=3,image_bytes=b'abc',created_at=datetime.utcnow()-timedelta(hours=7)); s.add(a); s.commit()
    assert cleanup_stale_artifacts(s, older_than_hours=6) == 1
    assert s.get(ImageGenerationArtifact, a.id).image_bytes is None

import pytest
from types import SimpleNamespace
from app.llm.image_client import VENICE_SEED_MIN, VENICE_SEED_MAX, venice_image_payload, ImageValidationError
from app.services.image_generation_service import deterministic_provider_seed, process_job


def test_venice_seed_normalization_range_and_no_minus_one():
    for raw in [1425953685, 1824167606, 1543286203, -1, 0, 1, 999999999]:
        payload=venice_image_payload('p','n',seed=raw)
        assert VENICE_SEED_MIN <= payload['seed'] <= VENICE_SEED_MAX
        assert payload['seed'] != -1
    assert VENICE_SEED_MIN <= deterministic_provider_seed('job22', 1425953685) <= VENICE_SEED_MAX


def test_job_seed_equals_metadata_seed_used_and_variations_differ():
    s=session(); u=User(telegram_id=7); s.add(u); s.flush()
    j1=ImageGenerationJob(idempotency_key='k1', correlation_id='c1', user_id=u.id, chat_id=1, seed=deterministic_provider_seed('same', 1), metadata_json={'seed_used':deterministic_provider_seed('same', 1)})
    j2=ImageGenerationJob(idempotency_key='k2', correlation_id='c2', user_id=u.id, chat_id=1, seed=deterministic_provider_seed('same', 2), metadata_json={'seed_used':deterministic_provider_seed('same', 2)})
    s.add_all([j1,j2]); s.commit()
    assert j1.seed == j1.metadata_json['seed_used']
    assert j2.seed == j2.metadata_json['seed_used']
    assert j1.seed != j2.seed


class _Client:
    def __init__(self): self.calls=[]
    async def generate(self, prompt, negative_prompt, *, width, height, seed, model=None):
        self.calls.append({'seed': seed, 'model': model})
        data = b'old-bytes' if len(self.calls)==1 else b'new-bytes'
        return SimpleNamespace(
            image_bytes=data,
            mime_type='image/png',
            request_id='r',
            width=width,
            height=height,
            latency_seconds=0.01,
            response_type='binary',
            metadata={
                'seed_used': seed,
                'seed_fallback_used': False,
            },
        )

class _NonRetryableClient:
    async def generate(
        self,
        prompt,
        negative_prompt,
        *,
        width,
        height,
        seed,
    ):
        raise ImageValidationError(
            '400:invalid provider payload'
        )


class _Telegram:
    async def send_photo_bytes(self, *args, **kwargs): return 123


def test_non_retryable_provider_error_fails_job_immediately():
    import asyncio

    async def run():
        s = session()

        user = User(
            telegram_id=77,
        )

        s.add(user)
        s.flush()

        job = ImageGenerationJob(
            idempotency_key='non-retryable',
            correlation_id='non-retryable',
            user_id=user.id,
            chat_id=1,
            status='processing',
            attempt_count=1,
            max_attempts=3,
            prompt='prompt',
            negative_prompt='negative',
            seed=123,
        )

        s.add(job)
        s.commit()

        result = await process_job(
            s,
            job,
            image_client=(
                _NonRetryableClient()
            ),
            telegram_service=_Telegram(),
        )

        assert result.status == 'failed'
        assert (
            result.error_code
            == 'provider_failure'
        )
        assert (
            '400:invalid provider payload'
            in result.error_message
        )

        assert result.attempt_count == 1

    asyncio.run(run())


def test_identical_checksum_triggers_one_controlled_variation_retry(monkeypatch):
    import asyncio
    async def _run():
        import app.services.image_generation_service as svc
        async def fake_archive(self, db, job): return False
        monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
        s=session(); u=User(telegram_id=9); s.add(u); s.flush()
        old=ImageGenerationJob(idempotency_key='old', correlation_id='old', user_id=u.id, chat_id=1, status='sent', sent_at=datetime.utcnow())
        s.add(old); s.flush(); s.add(ImageGenerationArtifact(job_id=old.id,mime_type='image/png',checksum=__import__('hashlib').sha256(b'old-bytes').hexdigest(),byte_size=9,image_bytes=b'old-bytes'))
        job=ImageGenerationJob(idempotency_key='new', correlation_id='new', user_id=u.id, chat_id=1, status='processing', prompt='p', negative_prompt='n', seed=123, metadata_json={'route_type':'image_followup'})
        s.add(job); s.commit()
        client=_Client()
        await process_job(s, job, image_client=client, telegram_service=_Telegram())
        assert len(client.calls) == 2
        assert client.calls[0] != client.calls[1]
        assert job.metadata_json['duplicate_retry_applied'] is True
        assert job.seed == job.metadata_json['seed_used']
    asyncio.run(_run())


def _png_with_metadata(text: str = "", *, color=(255, 255, 255)) -> bytes:
    from io import BytesIO
    from PIL import Image, PngImagePlugin

    image = Image.new('RGB', (640, 360), color)
    info = PngImagePlugin.PngInfo()
    if text:
        info.add_text('provider_message', text)
    out = BytesIO()
    image.save(out, format='PNG', pnginfo=info)
    return out.getvalue()


def _font(size=28):
    from PIL import ImageFont
    for path in (
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf',
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _text_card(*, background=(0, 0, 0), foreground=(245, 245, 245)) -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1024, 1024), background)
    draw = ImageDraw.Draw(image)
    font = _font(30)
    blocks = [
        'Our systems have detected content',
        'that violates our terms of service.',
        '',
        'Please try changing your prompt,',
        'or trying another model.',
        '',
        'If you believe this is an error,',
        'please contact support@venice.ai.',
    ]
    y = 360
    for line in blocks:
        if not line:
            y += 30
            continue
        box = draw.textbbox((0, 0), line, font=font)
        x = (1024 - (box[2] - box[0])) // 2
        draw.text((x, y), line, fill=foreground, font=font)
        y += 43
    out = BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def _night_photo_like() -> bytes:
    from io import BytesIO
    from PIL import Image
    image = Image.new('RGB', (1024, 1024))
    px = image.load()
    for y in range(1024):
        for x in range(1024):
            base = int(8 + 38 * y / 1023)
            noise = ((x * 17 + y * 31) % 23) - 11
            px[x, y] = (max(0, base + noise), max(0, base + noise // 2), max(8, base + 18 + noise))
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()


def _portrait_like() -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1024, 1024), (45, 45, 45))
    draw = ImageDraw.Draw(image)
    draw.ellipse((360, 160, 664, 464), fill=(165, 165, 165))
    draw.rounded_rectangle((285, 450, 740, 980), radius=160, fill=(115, 115, 115))
    draw.rectangle((0, 760, 1024, 1024), fill=(35, 35, 35))
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()


def _bathroom_like() -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1024, 1024), (205, 197, 188))
    draw = ImageDraw.Draw(image)
    for x in range(0, 1024, 128):
        draw.line((x, 0, x, 1024), fill=(170, 165, 160), width=3)
    for y in range(0, 1024, 128):
        draw.line((0, y, 1024, y), fill=(170, 165, 160), width=3)
    draw.rectangle((320, 120, 760, 720), outline=(90, 90, 90), width=16, fill=(185, 195, 200))
    draw.ellipse((430, 260, 610, 440), fill=(120, 105, 95))
    draw.rounded_rectangle((390, 430, 650, 820), radius=90, fill=(80, 80, 80))
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()


class _SequenceClient:
    def __init__(self, images):
        self.images = list(images)
        self.calls = []

    async def generate(self, prompt, negative_prompt, *, width, height, seed, model=None):
        self.calls.append({'seed': seed, 'model': model})
        image = self.images.pop(0)
        return SimpleNamespace(
            image_bytes=image,
            mime_type='image/png',
            request_id=f'r{len(self.calls)}',
            width=width,
            height=height,
            latency_seconds=0.01,
            response_type='binary',
            metadata={'seed_used': seed, 'seed_fallback_used': False},
        )


class _RecordingTelegram:
    def __init__(self):
        self.photos = []
        self.texts = []

    async def send_photo_bytes(self, *args, **kwargs):
        self.photos.append((args, kwargs))
        return 456

    async def send_text(self, chat_id, text, **kwargs):
        self.texts.append((chat_id, text, kwargs))
        return 789


def test_provider_error_screen_detector_flags_pixel_cards_and_allows_scenes():
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    dark_card = _text_card(background=(0, 0, 0), foreground=(245, 245, 245))
    light_card = _text_card(background=(255, 255, 255), foreground=(20, 20, 20))

    dark_detection = detect_provider_error_screen(dark_card)
    light_detection = detect_provider_error_screen(light_card)

    assert dark_detection.is_error_screen is True
    assert dark_detection.reason == 'dark_text_only_provider_error_screen'
    assert light_detection.is_error_screen is True
    assert light_detection.reason == 'light_text_only_provider_error_screen'
    assert detect_provider_error_screen(_night_photo_like()).is_error_screen is False
    assert detect_provider_error_screen(_portrait_like()).is_error_screen is False
    assert detect_provider_error_screen(_bathroom_like()).is_error_screen is False



def test_provider_error_screen_first_attempt_retries_then_sends_success(monkeypatch):
    import asyncio
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)

    async def run():
        s = session(); u = User(telegram_id=88); s.add(u); s.flush()
        job = ImageGenerationJob(idempotency_key='screen-then-ok', correlation_id='c', user_id=u.id, chat_id=1, status='processing', attempt_count=1, max_attempts=2, prompt='p', negative_prompt='n', seed=123)
        s.add(job); s.commit()
        screen = _text_card(background=(0, 0, 0), foreground=(245, 245, 245))
        ok = _png_with_metadata('valid generated image', color=(20, 90, 140))
        tg = _RecordingTelegram(); client = _SequenceClient([screen, ok])

        result = await process_job(s, job, image_client=client, telegram_service=tg)
        assert result.status == 'sent'
        assert result.error_code is None
        assert len(tg.photos) == 1
        assert result.metadata_json['moderation_screen_detected'] is False
        assert result.metadata_json['fallback_model_used'] is True
        assert len(result.metadata_json['provider_model_attempts']) == 2
        assert result.metadata_json['provider_model_attempts'][0]['moderation_screen_detected'] is True
        assert result.metadata_json['provider_model_attempts'][1]['moderation_screen_detected'] is False
        assert client.calls[0]['model'] != client.calls[1]['model']

    asyncio.run(run())


def test_all_provider_error_screen_attempts_fail_without_artifact_delivery():
    import asyncio

    async def run():
        s = session(); u = User(telegram_id=99); s.add(u); s.flush()
        job = ImageGenerationJob(idempotency_key='screen-final', correlation_id='c', user_id=u.id, chat_id=1, status='processing', attempt_count=1, max_attempts=1, prompt='p', negative_prompt='n', seed=123)
        s.add(job); s.commit()
        screen = _text_card(background=(0, 0, 0), foreground=(245, 245, 245))
        tg = _RecordingTelegram()

        result = await process_job(s, job, image_client=_SequenceClient([screen, screen]), telegram_service=tg)
        artifact = s.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id == job.id))
        assert result.status == 'failed'
        assert result.error_code == 'provider_policy_block'
        assert result.error_message == 'provider returned moderation screen image'
        assert tg.photos == []
        assert tg.texts
        assert artifact is None or not artifact.image_bytes
        assert result.metadata_json['moderation_screen_detected'] is True

    asyncio.run(run())


def test_delivery_guard_blocks_existing_moderation_artifact(monkeypatch):
    import asyncio
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)

    async def run():
        s = session(); u = User(telegram_id=101); s.add(u); s.flush()
        job = ImageGenerationJob(idempotency_key='guard', correlation_id='c', user_id=u.id, chat_id=1, status='processing', attempt_count=1, max_attempts=1, prompt='p', negative_prompt='n', seed=123)
        s.add(job); s.flush()
        s.add(ImageGenerationArtifact(job_id=job.id, mime_type='image/png', checksum='bad', byte_size=1, image_bytes=_text_card(background=(0, 0, 0), foreground=(245, 245, 245))))
        s.commit()
        tg = _RecordingTelegram()

        result = await process_job(s, job, image_client=_SequenceClient([]), telegram_service=tg)
        artifact = s.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id == job.id))
        assert result.status == 'failed'
        assert result.error_code == 'provider_policy_block'
        assert tg.photos == []
        assert tg.texts
        assert artifact.image_bytes is None

    asyncio.run(run())


def test_ordinary_valid_image_still_delivers(monkeypatch):
    import asyncio
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)

    async def run():
        s = session(); u = User(telegram_id=100); s.add(u); s.flush()
        job = ImageGenerationJob(idempotency_key='valid-image', correlation_id='c', user_id=u.id, chat_id=1, status='processing', attempt_count=1, max_attempts=1, prompt='p', negative_prompt='n', seed=123)
        s.add(job); s.commit()
        tg = _RecordingTelegram()
        result = await process_job(s, job, image_client=_SequenceClient([_png_with_metadata('valid generated image', color=(50, 120, 80))]), telegram_service=tg)
        assert result.status == 'sent'
        assert len(tg.photos) == 1
        assert result.error_code is None

    asyncio.run(run())
