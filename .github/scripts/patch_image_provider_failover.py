from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


service_path = Path("app/services/image_generation_service.py")
service = service_path.read_text()

service = replace_once(
    service,
    "from app.llm.image_client import VeniceImageClient, ImageClientError, image_resolution_tier, DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS, DEFAULT_CFG_SCALE, DEFAULT_SEED, VENICE_SEED_MIN, VENICE_SEED_MAX, normalize_venice_seed, validate_image_dimensions\n",
    "from app.llm.image_client import VeniceImageClient, ImageClientError, ImageAuthError, ImageBalanceError, ImageValidationError, image_resolution_tier, DEFAULT_IMAGE_MODEL, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_STEPS, DEFAULT_CFG_SCALE, DEFAULT_SEED, VENICE_SEED_MIN, VENICE_SEED_MAX, normalize_venice_seed, validate_image_dimensions\n",
    "image client imports",
)

service = replace_once(
    service,
    """    runtime_settings=get_settings()
    generation_model=select_generation_model(content_classification=intent.content_classification, default_model=DEFAULT_IMAGE_MODEL, adult_model=getattr(runtime_settings, 'image_generation_adult_model', None))
""",
    """    runtime_settings=get_settings()
    configured_default_model=(getattr(runtime_settings, 'image_generation_model', None) or DEFAULT_IMAGE_MODEL).strip()
    generation_model=select_generation_model(content_classification=intent.content_classification, default_model=configured_default_model, adult_model=getattr(runtime_settings, 'image_generation_adult_model', None))
""",
    "configured primary generation model",
)

old_plan = """            primary_model = (meta.get('primary_generation_model') or job.model or DEFAULT_IMAGE_MODEL)
            fallback_model = (getattr(settings, 'image_generation_fallback_model', '') or '').strip()
            model_plan = [primary_model]
            if fallback_model and fallback_model not in model_plan:
                model_plan.append(fallback_model)
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'final_generation_model':None}
            res = None
            detection = None
            successful_model = None
            moderation_checksums=[]
            rejected_quality=[]
            accepted_qa=None
"""
new_plan = """            primary_model = (meta.get('primary_generation_model') or job.model or getattr(settings, 'image_generation_model', None) or DEFAULT_IMAGE_MODEL).strip()
            visual_requirements = meta.get('visual_requirements') or {}
            adult_generation = bool(visual_requirements.get('anatomy_qa_required') or visual_requirements.get('explicit_nudity_requested'))
            if adult_generation:
                fallback_model = (getattr(settings, 'image_generation_adult_fallback_model', '') or '').strip()
                emergency_models = []
            else:
                fallback_model = (getattr(settings, 'image_generation_fallback_model', '') or '').strip()
                emergency_models = [part.strip() for part in str(getattr(settings, 'image_generation_emergency_models', '') or '').split(',') if part.strip()]
            configured_model_plan = []
            for candidate_model in [primary_model, fallback_model, *emergency_models]:
                if candidate_model and candidate_model not in configured_model_plan:
                    configured_model_plan.append(candidate_model)
            model_plan = list(configured_model_plan)
            available_models = None
            availability_method = getattr(client, 'available_image_models', None)
            if callable(availability_method):
                try:
                    available_models = await availability_method()
                except Exception as discovery_exc:
                    logger.warning('IMAGE_PROVIDER_MODEL_DISCOVERY_FAILED_IN_WORKER job_id=%s error_type=%s', job.id, type(discovery_exc).__name__)
            skipped_unavailable_models = []
            if available_models is not None:
                skipped_unavailable_models = [model for model in model_plan if model not in available_models]
                model_plan = [model for model in model_plan if model in available_models]
                if skipped_unavailable_models:
                    logger.warning('IMAGE_PROVIDER_MODELS_SKIPPED_UNAVAILABLE job_id=%s models=%s', job.id, skipped_unavailable_models)
            if not model_plan:
                raise ImageValidationError('no_configured_image_model_available')
            job.metadata_json={**meta,'primary_generation_model':primary_model,'fallback_generation_model':fallback_model or None,'configured_generation_model_plan':configured_model_plan,'effective_generation_model_plan':model_plan,'skipped_unavailable_generation_models':skipped_unavailable_models,'final_generation_model':None}
            res = None
            detection = None
            successful_model = None
            last_provider_error = None
            moderation_checksums=[]
            rejected_quality=[]
            accepted_qa=None
"""
service = replace_once(service, old_plan, new_plan, "provider model plan")

old_generate = """                try:
                    attempt_seed, norm_applied = normalize_venice_seed(job.seed, salt=f'job:{job.id}:{attempt_model}')
                    job.metadata_json={**(job.metadata_json or {}),'normalized_provider_seed':attempt_seed,'seed_normalization_applied': bool((job.metadata_json or {}).get('seed_normalization_applied') or norm_applied),'seed_provider_min':VENICE_SEED_MIN,'seed_provider_max':VENICE_SEED_MAX}
                    attempt_prompt=(job.prompt or '') + (corrective_prompt_for_reasons(rejected_quality[-1]['reason_codes'], expected_subject_count=int((job.metadata_json or {}).get('expected_subject_count', 1)), expected_interaction=(job.metadata_json or {}).get('interaction'), secondary_subject_role=(job.metadata_json or {}).get('secondary_subject_role'), identity_requirements=(job.metadata_json or {}).get('identity_descriptor'), photo_contract=((job.metadata_json or {}).get('visual_requirements') or {}).get('photo_contract')) if rejected_quality and attempt_index > 0 else '')
                    res=await client.generate(attempt_prompt, job.negative_prompt or '', width=job.width, height=job.height, seed=attempt_seed, model=attempt_model)
                except TypeError:
                    res=await client.generate(job.prompt or '', job.negative_prompt or '', width=job.width, height=job.height, seed=job.seed)
                    attempt_seed=job.seed
                detection=detect_provider_error_screen(res.image_bytes)
"""
new_generate = """                attempt_seed, norm_applied = normalize_venice_seed(job.seed, salt=f'job:{job.id}:{attempt_model}')
                job.metadata_json={**(job.metadata_json or {}),'normalized_provider_seed':attempt_seed,'seed_normalization_applied': bool((job.metadata_json or {}).get('seed_normalization_applied') or norm_applied),'seed_provider_min':VENICE_SEED_MIN,'seed_provider_max':VENICE_SEED_MAX}
                attempt_prompt=(job.prompt or '') + (corrective_prompt_for_reasons(rejected_quality[-1]['reason_codes'], expected_subject_count=int((job.metadata_json or {}).get('expected_subject_count', 1)), expected_interaction=(job.metadata_json or {}).get('interaction'), secondary_subject_role=(job.metadata_json or {}).get('secondary_subject_role'), identity_requirements=(job.metadata_json or {}).get('identity_descriptor'), photo_contract=((job.metadata_json or {}).get('visual_requirements') or {}).get('photo_contract')) if rejected_quality and attempt_index > 0 else '')
                try:
                    try:
                        res=await client.generate(attempt_prompt, job.negative_prompt or '', width=job.width, height=job.height, seed=attempt_seed, model=attempt_model)
                    except TypeError:
                        res=await client.generate(attempt_prompt, job.negative_prompt or '', width=job.width, height=job.height, seed=attempt_seed)
                except (ImageAuthError, ImageBalanceError):
                    raise
                except ImageClientError as provider_exc:
                    last_provider_error = provider_exc
                    attempts=list((job.metadata_json or {}).get('provider_model_attempts') or [])
                    attempts.append({
                        'provider': job.provider,
                        'model': attempt_model,
                        'seed': attempt_seed,
                        'error_type': type(provider_exc).__name__,
                        'error_code': getattr(provider_exc, 'code', 'image_error'),
                        'retryable': bool(getattr(provider_exc, 'retryable', False)),
                        'error_detail': str(provider_exc)[:300],
                    })
                    job.metadata_json={**(job.metadata_json or {}),'provider_model_attempts':attempts,'last_provider_error_model':attempt_model,'last_provider_error_code':getattr(provider_exc, 'code', 'image_error')}
                    logger.warning('IMAGE_PROVIDER_MODEL_FAILED job_id=%s user_id=%s model=%s error_type=%s error_code=%s has_next_model=%s', job.id, job.user_id, attempt_model, type(provider_exc).__name__, getattr(provider_exc, 'code', 'image_error'), attempt_index + 1 < len(model_plan))
                    if attempt_index + 1 < len(model_plan):
                        continue
                    raise
                detection=detect_provider_error_screen(res.image_bytes)
"""
service = replace_once(service, old_generate, new_generate, "per-model provider failover")

service = replace_once(
    service,
    """            if res is None or successful_model is None:
                if rejected_quality:
                    logger.warning('IMAGE_SINGLE_SUBJECT_FINAL_FAILED job_id=%s user_id=%s chat_id=%s reason_codes=%s', job.id, job.user_id, job.chat_id, rejected_quality[-1].get('reason_codes'))
                    raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
                raise ProviderPolicyScreenError('provider returned moderation screen image')
""",
    """            if res is None or successful_model is None:
                if rejected_quality:
                    logger.warning('IMAGE_SINGLE_SUBJECT_FINAL_FAILED job_id=%s user_id=%s chat_id=%s reason_codes=%s', job.id, job.user_id, job.chat_id, rejected_quality[-1].get('reason_codes'))
                    raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
                if last_provider_error is not None:
                    raise last_provider_error
                raise ProviderPolicyScreenError('provider returned moderation screen image')
""",
    "preserve final provider error",
)

service_path.write_text(service)


test_path = Path("tests/test_image_provider_failover.py")
test_path.write_text('''import asyncio
import base64
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.llm.image_client import (
    ImageGenerationResponse,
    ImageValidationError,
    _extract_json_image,
    build_venice_image_payload,
)
from app.models.addon import AddonProduct, UserAddon
from app.models.billing import UsageCharge
from app.models.image_generation import (
    ImageGenerationArtifact,
    ImageGenerationFeedback,
    ImageGenerationJob,
    PartnerVisualProfile,
)
from app.models.memory import MemoryItem
from app.models.usage import AiUsageEvent
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__, Wallet.__table__, WalletTransaction.__table__,
            AddonProduct.__table__, UserAddon.__table__, UsageCharge.__table__,
            AiUsageEvent.__table__, PartnerVisualProfile.__table__,
            ImageGenerationJob.__table__, ImageGenerationArtifact.__table__,
            ImageGenerationFeedback.__table__, MemoryItem.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def _png_bytes():
    output = BytesIO()
    Image.new("RGB", (64, 64), (220, 220, 220)).save(output, format="PNG")
    return output.getvalue()


async def _pass_qa(*args, **kwargs):
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    return GeneratedImageQAResult(True, 1, 1, False, False, False, False, False, False, "high", [], "test-qa")


class _Telegram:
    async def send_photo_bytes(self, *args, **kwargs):
        return 123


class _FailoverClient:
    def __init__(self):
        self.calls = []

    async def available_image_models(self):
        return None

    async def generate(self, prompt, negative_prompt, *, width, height, seed, model):
        self.calls.append(model)
        if model == "krea-2-turbo":
            raise ImageValidationError("model_unavailable:krea-2-turbo")
        return ImageGenerationResponse(
            image_bytes=_png_bytes(),
            mime_type="image/png",
            request_id="request-2",
            model=model,
            width=width,
            height=height,
            latency_seconds=0.01,
            response_type="binary",
            metadata={"seed_used": seed, "seed_fallback_used": False, "payload_profile": "test"},
        )


class _DiscoveryClient(_FailoverClient):
    async def available_image_models(self):
        return {"seedream-v5-lite"}


def _job(session, user, *, model="krea-2-turbo"):
    job = ImageGenerationJob(
        idempotency_key=f"provider-{model}",
        correlation_id=f"provider-{model}",
        user_id=user.id,
        chat_id=1,
        status="processing",
        attempt_count=1,
        max_attempts=3,
        prompt="prompt",
        negative_prompt="negative",
        seed=123,
        model=model,
        width=1024,
        height=1280,
        metadata_json={"expected_subject_count": 1, "visual_requirements": {}},
    )
    session.add(job)
    session.commit()
    return job


def test_seedream_payload_uses_resolution_tier_fields_only():
    payload = build_venice_image_payload(
        model="seedream-v5-lite",
        prompt="p",
        negative_prompt="n",
        width=1024,
        height=1280,
        seed=5,
    )
    assert payload["aspect_ratio"] == "4:5"
    assert payload["resolution"] == "1K"
    assert "width" not in payload and "height" not in payload
    assert "steps" not in payload and "cfg_scale" not in payload


def test_native_json_images_string_is_decoded():
    raw = b"image-bytes"
    decoded, mime = _extract_json_image({"images": [base64.b64encode(raw).decode()], "format": "webp"})
    assert decoded == raw
    assert mime == "image/webp"


def test_retired_primary_model_falls_through_to_current_fallback(monkeypatch):
    import app.services.image_generation_service as service

    async def run():
        session = _session()
        user = User(telegram_id=501)
        session.add(user)
        session.flush()
        job = _job(session, user)
        client = _FailoverClient()
        monkeypatch.setattr(
            service,
            "get_settings",
            lambda: SimpleNamespace(
                image_generation_model="seedream-v5-lite",
                image_generation_fallback_model="seedream-v5-lite",
                image_generation_emergency_models="venice-sd35",
                image_generation_adult_fallback_model="lustify-v8",
            ),
        )
        monkeypatch.setattr(service.GeneratedMediaArchiveService, "archive_image", lambda *args, **kwargs: asyncio.sleep(0, result=False))
        result = await service.process_job(
            session,
            job,
            image_client=client,
            telegram_service=_Telegram(),
            generated_image_qa_evaluator=_pass_qa,
        )
        assert result.status == "sent"
        assert client.calls[:2] == ["krea-2-turbo", "seedream-v5-lite"]
        assert result.metadata_json["final_generation_model"] == "seedream-v5-lite"
        first_attempt = result.metadata_json["provider_model_attempts"][0]
        assert first_attempt["error_code"] == "validation"

    asyncio.run(run())


def test_model_discovery_skips_retired_model_before_generation(monkeypatch):
    import app.services.image_generation_service as service

    async def run():
        session = _session()
        user = User(telegram_id=502)
        session.add(user)
        session.flush()
        job = _job(session, user)
        client = _DiscoveryClient()
        monkeypatch.setattr(
            service,
            "get_settings",
            lambda: SimpleNamespace(
                image_generation_model="seedream-v5-lite",
                image_generation_fallback_model="seedream-v5-lite",
                image_generation_emergency_models="venice-sd35",
                image_generation_adult_fallback_model="lustify-v8",
            ),
        )
        monkeypatch.setattr(service.GeneratedMediaArchiveService, "archive_image", lambda *args, **kwargs: asyncio.sleep(0, result=False))
        result = await service.process_job(
            session,
            job,
            image_client=client,
            telegram_service=_Telegram(),
            generated_image_qa_evaluator=_pass_qa,
        )
        assert result.status == "sent"
        assert client.calls == ["seedream-v5-lite"]
        assert result.metadata_json["skipped_unavailable_generation_models"] == ["krea-2-turbo", "venice-sd35"]

    asyncio.run(run())
''')

print("patch_image_provider_failover: ok")
