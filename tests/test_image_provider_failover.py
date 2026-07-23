import asyncio
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
        assert set(result.metadata_json["skipped_unavailable_generation_models"]) == {"krea-2-turbo", "venice-sd35"}

    asyncio.run(run())
