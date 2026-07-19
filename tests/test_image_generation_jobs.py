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
from app.models.memory import MemoryItem
from app.services.image_generation_service import claim_next_job, cleanup_stale_artifacts

def session():
    e=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(e, tables=[User.__table__, Wallet.__table__, WalletTransaction.__table__, AddonProduct.__table__, UserAddon.__table__, UsageCharge.__table__, AiUsageEvent.__table__, PartnerVisualProfile.__table__, ImageGenerationJob.__table__, ImageGenerationArtifact.__table__, ImageGenerationFeedback.__table__, MemoryItem.__table__])
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


def _passing_generated_image_qa():
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    return GeneratedImageQAResult(True,1,1,False,False,False,False,False,False,'high',[],'test-qa')

async def _pass_qa(*args, **kwargs):
    return _passing_generated_image_qa()


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
        await process_job(s, job, image_client=client, telegram_service=_Telegram(), generated_image_qa_evaluator=_pass_qa)
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



def _production_shaped_venice_card() -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw

    image = Image.new('RGB', (1024, 1024), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(32)
    lines = [
        'The image generation systems have detected content',
        'that violates the image provider terms of service.',
        'Please try changing your prompt and try again.',
        'You can also try generating with another model.',
        'If you believe this is an error, contact support.',
        'No image was generated for this request.',
    ]
    y = 265
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        x = (1024 - (box[2] - box[0])) // 2
        draw.text((x, y), line, fill=(245, 245, 245), font=font)
        y += 86
    out = BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def _dark_noise_photo_like() -> bytes:
    from io import BytesIO
    from PIL import Image
    image = Image.new('RGB', (1024, 1024))
    px = image.load()
    for y in range(1024):
        for x in range(1024):
            v = (x * 13 + y * 29 + (x * y) % 47) % 90
            px[x, y] = (v // 2, max(0, v // 2 - 6), max(0, v // 2 - 12))
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()


def _person_black_clothes_like() -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1024, 1024), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    draw.ellipse((390, 110, 635, 355), fill=(152, 139, 128))
    draw.rounded_rectangle((300, 350, 725, 980), radius=130, fill=(12, 12, 12), outline=(70, 70, 70), width=8)
    draw.line((512, 360, 512, 930), fill=(55, 55, 55), width=9)
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()


def _one_large_logo_card() -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1024, 1024), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((312, 312, 712, 712), outline=(245, 245, 245), width=36)
    draw.rectangle((450, 450, 574, 574), fill=(245, 245, 245))
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()


def _short_caption_card(lines=2) -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (1024, 1024), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(48)
    for i, line in enumerate(['Short caption', 'Try again'][:lines]):
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(((1024 - (box[2] - box[0])) // 2, 450 + i * 72), line, fill=(245, 245, 245), font=font)
    out = BytesIO(); image.save(out, format='PNG'); return out.getvalue()

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


def test_provider_error_screen_detector_flags_real_venice_geometry_and_boundaries():
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    detection = detect_provider_error_screen(_production_shaped_venice_card())

    assert detection.is_error_screen is True
    assert detection.reason == 'dark_provider_moderation_card'
    assert detection.diagnostics['margin_x'] >= 0.07
    assert detection.diagnostics['box_width_ratio'] >= 0.84
    assert detection.diagnostics['box_width_ratio'] <= 0.87
    assert detection.diagnostics['band_count'] == 6


def test_provider_error_screen_detector_rejects_dark_non_cards():
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    assert detect_provider_error_screen(_dark_noise_photo_like()).is_error_screen is False
    assert detect_provider_error_screen(_portrait_like()).is_error_screen is False
    assert detect_provider_error_screen(_night_photo_like()).is_error_screen is False
    assert detect_provider_error_screen(_person_black_clothes_like()).is_error_screen is False
    assert detect_provider_error_screen(_one_large_logo_card()).is_error_screen is False
    assert detect_provider_error_screen(_short_caption_card(1)).is_error_screen is False
    assert detect_provider_error_screen(_short_caption_card(2)).is_error_screen is False


def test_provider_error_screen_detector_flags_pixel_cards_and_allows_scenes():
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    dark_card = _text_card(background=(0, 0, 0), foreground=(245, 245, 245))
    light_card = _text_card(background=(255, 255, 255), foreground=(20, 20, 20))

    dark_detection = detect_provider_error_screen(dark_card)
    light_detection = detect_provider_error_screen(light_card)

    assert dark_detection.is_error_screen is True
    assert dark_detection.reason in {'dark_provider_moderation_card', 'dark_text_only_provider_error_screen'}
    assert light_detection.is_error_screen is True
    assert light_detection.reason == 'light_text_only_provider_error_screen'
    assert detect_provider_error_screen(_night_photo_like()).is_error_screen is False
    assert detect_provider_error_screen(_portrait_like()).is_error_screen is False
    assert detect_provider_error_screen(_bathroom_like()).is_error_screen is False



def test_provider_error_screen_first_attempt_retries_then_sends_success(monkeypatch, caplog):
    import asyncio
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)

    async def run():
        s = session(); u = User(telegram_id=88); s.add(u); s.flush()
        job = ImageGenerationJob(idempotency_key='screen-then-ok', correlation_id='c', user_id=u.id, chat_id=1, status='processing', attempt_count=1, max_attempts=2, prompt='p', negative_prompt='n', seed=123)
        s.add(job); s.commit()
        screen = _production_shaped_venice_card()
        ok = _png_with_metadata('valid generated image', color=(20, 90, 140))
        tg = _RecordingTelegram(); client = _SequenceClient([screen, ok])

        result = await process_job(s, job, image_client=client, telegram_service=tg, generated_image_qa_evaluator=_pass_qa)
        assert result.status == 'sent'
        assert result.error_code is None
        assert len(tg.photos) == 1
        assert tg.photos[0][0][1] == ok
        assert tg.photos[0][0][1] != screen
        assert 'IMAGE_PROVIDER_ERROR_SCREEN_DETECTED' in caplog.text
        assert result.metadata_json['moderation_screen_detected'] is False
        assert result.metadata_json['fallback_model_used'] is True
        assert len(result.metadata_json['provider_model_attempts']) == 2
        assert result.metadata_json['provider_model_attempts'][0]['moderation_screen_detected'] is True
        assert result.metadata_json['provider_model_attempts'][0]['moderation_screen_reason'] == 'dark_provider_moderation_card'
        assert 'detector_metrics' in result.metadata_json['provider_model_attempts'][0]
        assert result.metadata_json['provider_model_attempts'][1]['moderation_screen_detected'] is False
        assert 'detector_metrics' in result.metadata_json['provider_model_attempts'][1]
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
        s.add(ImageGenerationArtifact(job_id=job.id, mime_type='image/png', checksum='bad', byte_size=1, image_bytes=_production_shaped_venice_card()))
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
        result = await process_job(s, job, image_client=_SequenceClient([_png_with_metadata('valid generated image', color=(50, 120, 80))]), telegram_service=tg, generated_image_qa_evaluator=_pass_qa)
        assert result.status == 'sent'
        assert len(tg.photos) == 1
        assert result.error_code is None

    asyncio.run(run())


def test_krea_policy_card_seedream_success_preserves_primary_model(monkeypatch):
    import asyncio
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)
    async def run():
        s=session(); u=User(telegram_id=10); s.add(u); s.flush()
        job=ImageGenerationJob(idempotency_key='p', correlation_id='p', user_id=u.id, chat_id=1, status='processing', attempt_count=1, prompt='p', negative_prompt='n', seed=123, model='krea-2-turbo')
        s.add(job); s.commit()
        class C:
            def __init__(self): self.calls=[]
            async def generate(self, prompt, negative_prompt, *, width, height, seed, model=None):
                self.calls.append(model)
                data=_production_shaped_venice_card() if model=='krea-2-turbo' else _png_with_metadata('ok')
                return SimpleNamespace(image_bytes=data,mime_type='image/png',request_id=f'r-{model}',width=width,height=height,latency_seconds=0.01,response_type='binary',metadata={'seed_used':seed,'payload_profile':'seedream_4_5_1k' if model=='seedream-v5-lite' else 'krea_1024x1280'})
        c=C(); await process_job(s, job, image_client=c, telegram_service=_Telegram(), generated_image_qa_evaluator=_pass_qa)
        assert c.calls == ['krea-2-turbo','seedream-v5-lite']
        assert 'lustify-sdxl' not in c.calls
        assert job.status == 'sent'
        assert job.model == 'krea-2-turbo'
        assert job.metadata_json['final_generation_model'] == 'seedream-v5-lite'
    asyncio.run(run())


def test_two_policy_cards_fail_without_third_retry(monkeypatch):
    import asyncio
    async def run():
        s=session(); u=User(telegram_id=11); s.add(u); s.flush()
        job=ImageGenerationJob(idempotency_key='p2', correlation_id='p2', user_id=u.id, chat_id=1, status='processing', attempt_count=1, max_attempts=3, prompt='p', negative_prompt='n', seed=123, model='krea-2-turbo')
        s.add(job); s.commit()
        class T(_Telegram):
            def __init__(self): self.texts=[]
            async def send_text(self, chat_id, text): self.texts.append(text); return 1
        class C:
            def __init__(self): self.calls=[]
            async def generate(self, prompt, negative_prompt, *, width, height, seed, model=None):
                self.calls.append(model)
                return SimpleNamespace(image_bytes=_production_shaped_venice_card(),mime_type='image/png',request_id=f'r-{model}',width=width,height=height,latency_seconds=0.01,response_type='binary',metadata={'seed_used':seed})
        c=C(); t=T(); await process_job(s, job, image_client=c, telegram_service=t)
        assert c.calls == ['krea-2-turbo','seedream-v5-lite']
        assert job.status == 'failed' and job.error_code == 'provider_policy_block'
        assert job.metadata_json['final_generation_model'] is None
        assert job.metadata_json['identical_provider_error_artifact'] is True
        assert len(t.texts) == 1
    asyncio.run(run())


def test_single_subject_qa_krea_fails_seedream_delivered_once(monkeypatch):
    import asyncio
    import app.services.image_generation_service as svc
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    calls=[]
    async def fake_qa(image_bytes, *, selfie_allowed, mirror_allowed, expected_subject_count=1):
        if image_bytes == _png_with_metadata('krea-two'):
            return GeneratedImageQAResult(False,2,2,True,False,False,False,False,False,'high',['multiple_people'],'qa')
        return GeneratedImageQAResult(True,1,1,False,False,False,False,False,False,'high',[],'qa')
    monkeypatch.setattr(svc, 'evaluate_single_subject_image', fake_qa)
    class C:
        async def generate(self, prompt, negative_prompt, *, width, height, seed, model=None):
            calls.append((model,prompt)); data=_png_with_metadata('krea-two') if model=='krea-2-turbo' else _png_with_metadata('seedream-one')
            return SimpleNamespace(image_bytes=data,mime_type='image/png',request_id=f'r-{model}',width=width,height=height,latency_seconds=0.01,response_type='binary',metadata={'seed_used':seed})
    class T:
        def __init__(self): self.photos=[]
        async def send_photo_bytes(self, chat_id, photo_bytes, **kw): self.photos.append(photo_bytes); return 999
    async def run():
        s=session(); u=User(telegram_id=50); s.add(u); s.flush()
        job=ImageGenerationJob(idempotency_key='qa1', correlation_id='qa1', user_id=u.id, chat_id=1, status='processing', prompt='p', negative_prompt='n', seed=123, model='krea-2-turbo', metadata_json={})
        s.add(job); s.commit(); t=T()
        await process_job(s, job, image_client=C(), telegram_service=t, generated_image_qa_evaluator=fake_qa)
        assert [c[0] for c in calls] == ['krea-2-turbo','seedream-v5-lite']
        assert t.photos == [_png_with_metadata('seedream-one')]
        assert job.status == 'sent'
        assert job.metadata_json['final_generation_model'] == 'seedream-v5-lite'
        assert job.metadata_json['generated_image_quality_failures'][0]['model'] == 'krea-2-turbo'
        assert job.metadata_json['generated_image_qa']['passed'] is True
    asyncio.run(run())


def test_single_subject_qa_both_models_fail_refunds_and_sends_no_photo(monkeypatch):
    import asyncio
    import app.services.image_generation_service as svc
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    async def fake_qa(*a, **k): return GeneratedImageQAResult(False,2,2,True,False,False,False,False,False,'high',['multiple_people'],'qa')
    monkeypatch.setattr(svc, 'evaluate_single_subject_image', fake_qa)
    class C:
        def __init__(self): self.calls=[]
        async def generate(self, prompt, negative_prompt, *, width, height, seed, model=None):
            self.calls.append(model); return SimpleNamespace(image_bytes=_png_with_metadata(model or ''),mime_type='image/png',request_id='r',width=width,height=height,latency_seconds=0.01,response_type='binary',metadata={'seed_used':seed})
    class T:
        def __init__(self): self.photos=[]; self.texts=[]
        async def send_photo_bytes(self, chat_id, photo_bytes, **kw): self.photos.append(photo_bytes); return 1
        async def send_text(self, chat_id, text): self.texts.append(text); return 2
    async def run():
        s=session(); u=User(telegram_id=51); s.add(u); s.flush()
        job=ImageGenerationJob(idempotency_key='qa2', correlation_id='qa2', user_id=u.id, chat_id=1, status='processing', prompt='p', negative_prompt='n', seed=123, model='krea-2-turbo', metadata_json={})
        s.add(job); s.commit(); c=C(); t=T()
        await process_job(s, job, image_client=c, telegram_service=t)
        assert c.calls == ['krea-2-turbo','seedream-v5-lite']
        assert t.photos == [] and t.texts
        assert job.status == 'failed'
        assert job.error_code == 'image_quality_single_subject_failed'
        art=s.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id==job.id))
        assert art is None or art.image_bytes is None
    asyncio.run(run())


def test_duplicate_variation_replacement_is_qa_checked_and_checksum_bound(monkeypatch):
    import asyncio, hashlib
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    async def run():
        s=session(); u=User(telegram_id=909); s.add(u); s.flush()
        old=ImageGenerationJob(idempotency_key='old2', correlation_id='old2', user_id=u.id, chat_id=1, status='sent', sent_at=datetime.utcnow())
        s.add(old); s.flush(); s.add(ImageGenerationArtifact(job_id=old.id,mime_type='image/png',checksum=hashlib.sha256(b'old-bytes').hexdigest(),byte_size=9,image_bytes=b'old-bytes'))
        job=ImageGenerationJob(idempotency_key='new2', correlation_id='new2', user_id=u.id, chat_id=1, status='processing', prompt='p', negative_prompt='n', seed=123, metadata_json={'route_type':'image_followup'})
        s.add(job); s.commit(); seen=[]
        async def qa(image_bytes, **kw):
            seen.append(image_bytes)
            return _passing_generated_image_qa()
        client=_Client()
        await process_job(s, job, image_client=client, telegram_service=_Telegram(), generated_image_qa_evaluator=qa)
        assert seen == [b'old-bytes', b'new-bytes']
        assert job.metadata_json['generated_image_qa']['artifact_checksum'] == hashlib.sha256(b'new-bytes').hexdigest()
    asyncio.run(run())


def test_no_external_vision_http_calls_in_image_generation_unit_path(monkeypatch):
    import asyncio, httpx
    import app.services.image_generation_service as svc
    async def fake_archive(self, db, job): return False
    monkeypatch.setattr(svc.GeneratedMediaArchiveService, 'archive_image', fake_archive)
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)
    async def blocked(*args, **kwargs):
        raise AssertionError('external Vision HTTP call attempted')
    monkeypatch.setattr(httpx.AsyncClient, 'post', blocked)
    async def run():
        s=session(); u=User(telegram_id=910); s.add(u); s.flush()
        job=ImageGenerationJob(idempotency_key='no-http', correlation_id='no-http', user_id=u.id, chat_id=1, status='processing', prompt='p', negative_prompt='n', seed=123)
        s.add(job); s.commit(); tg=_RecordingTelegram()
        result=await process_job(s, job, image_client=_SequenceClient([_png_with_metadata('valid')]), telegram_service=tg, generated_image_qa_evaluator=_pass_qa)
        assert result.status == 'sent'
        assert len(tg.photos) == 1
    asyncio.run(run())


def test_two_subject_composition_qa_allows_expected_second_person():
    from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload
    qa = evaluate_generated_image_composition_payload({
        'person_count': 2,
        'face_count': 2,
        'intended_subject_count': 2,
        'second_person_visible': True,
        'unexpected_additional_person_visible': False,
        'background_extra_person_visible': False,
        'duplicate_subject_visible': False,
        'reflected_extra_person_visible': False,
        'interaction_detected': 'kiss',
        'interaction_matches_request': True,
        'confidence': 'high',
    }, expected_subject_count=2, expected_interaction='kiss')
    assert qa.passed is True
    assert 'multiple_people' not in qa.reason_codes
    assert 'too_many_people' not in qa.reason_codes


def test_two_subject_composition_qa_rejects_missing_extra_and_wrong_interaction():
    from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload
    missing = evaluate_generated_image_composition_payload({'person_count': 1, 'face_count': 1, 'confidence': 'high'}, expected_subject_count=2, expected_interaction='kiss')
    assert 'missing_secondary_subject' in missing.reason_codes
    too_many = evaluate_generated_image_composition_payload({'person_count': 3, 'face_count': 3, 'confidence': 'high'}, expected_subject_count=2, expected_interaction='kiss')
    assert 'too_many_people' in too_many.reason_codes
    background = evaluate_generated_image_composition_payload({'person_count': 2, 'face_count': 2, 'background_extra_person_visible': True, 'interaction_detected': 'kiss', 'interaction_matches_request': True, 'confidence': 'high'}, expected_subject_count=2, expected_interaction='kiss')
    assert 'unrelated_background_person' in background.reason_codes
    wrong = evaluate_generated_image_composition_payload({'person_count': 2, 'face_count': 2, 'intended_subject_count': 2, 'interaction_detected': 'hug', 'interaction_matches_request': False, 'confidence': 'high'}, expected_subject_count=2, expected_interaction='kiss')
    assert 'requested_interaction_missing' in wrong.reason_codes


def test_full_body_headshot_qa_metadata_blocks_delivery_status_sent(monkeypatch):
    import asyncio, hashlib
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa, GeneratedImageQAResult, corrective_prompt_for_reasons
    import app.services.image_generation_service as svc
    monkeypatch.setattr(svc, 'record_media_delivery', lambda *a, **k: None)
    async def run():
        s=session(); u=User(telegram_id=997); s.add(u); s.flush()
        data=_png_with_metadata('headshot')
        job=ImageGenerationJob(idempotency_key='fb-gate', correlation_id='fb-gate', user_id=u.id, chat_id=1, status='sending', prompt='p', negative_prompt='n', seed=123, metadata_json={'visual_requirements':{'framing_requirement':'full_body','full_body_visible':True},'full_body_required':True,'qa_requested_framing':'full_body','generated_image_qa':GeneratedImageQAResult(True,1,1,False,False,False,False,False,False,'high',[],'qa',framing_matches_request=False,requested_full_body_visible=True,head_inside_frame=True,feet_inside_frame=False,body_not_cropped=False).to_metadata(artifact_checksum=hashlib.sha256(data).hexdigest())})
        s.add(job); s.flush(); s.add(ImageGenerationArtifact(job_id=job.id,mime_type='image/png',checksum=hashlib.sha256(data).hexdigest(),byte_size=len(data),image_bytes=data)); s.commit()
        tg=_RecordingTelegram()
        result=await process_job(s, job, image_client=_SequenceClient([]), telegram_service=tg, generated_image_qa_evaluator=_pass_qa)
        assert result.status == 'failed'
        assert tg.photos == []
        assert not metadata_has_valid_generated_image_qa(job.metadata_json, data)
    asyncio.run(run())
    retry = corrective_prompt_for_reasons(['framing_mismatch','missing_feet'], expected_subject_count=1)
    for term in ['full body visible','full figure head-to-feet','camera farther away','no close-up','no crop']:
        assert term in retry
