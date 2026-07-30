import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.llm.image_client import ImageGenerationResponse, ImageValidationError
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
from app.services.generated_image_qa_service import GeneratedImageQAResult


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Wallet.__table__,
            WalletTransaction.__table__,
            AddonProduct.__table__,
            UserAddon.__table__,
            UsageCharge.__table__,
            AiUsageEvent.__table__,
            PartnerVisualProfile.__table__,
            ImageGenerationJob.__table__,
            ImageGenerationArtifact.__table__,
            ImageGenerationFeedback.__table__,
            MemoryItem.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def _png_bytes(level: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (64, 64), (level, level, level)).save(output, format="PNG")
    return output.getvalue()


def _settings():
    return SimpleNamespace(
        image_generation_preferred_model="krea-2-turbo",
        image_generation_model="krea-2-turbo",
        image_generation_fallback_model="seedream-v5-lite",
        image_generation_emergency_models="venice-sd35,z-image-turbo",
        image_generation_adult_preferred_model="krea-2-turbo",
        image_generation_adult_model="krea-2-turbo",
        image_generation_adult_fallback_model="seedream-v5-lite",
        image_generation_adult_emergency_models="",
        image_generation_adult_max_generation_attempts=3,
    )


def _job(session, user):
    visual_requirements = {
        "explicit_nudity_requested": True,
        "anatomy_qa_required": True,
        "anatomical_profile": "female",
        "framing_requirement": "full_body",
        "full_body_visible": True,
        "head_visible": True,
        "feet_visible": True,
        "body_not_cropped": True,
        "environment_visibility_required": True,
        "must_satisfy": {"required_scene_elements": ["private_indoor", "mirror"]},
        "photo_contract": {},
    }
    job = ImageGenerationJob(
        idempotency_key="krea-adult-worker",
        correlation_id="krea-adult-worker",
        user_id=user.id,
        chat_id=1,
        status="processing",
        attempt_count=1,
        max_attempts=3,
        prompt=(
            "Exactly one fictional adult. Natural full-body mirror selfie in a private room. "
            "Complete full figure visible from head to feet. Visibly fully nude."
        ),
        negative_prompt="extra people, cropped body, missing feet, clothing, watermark",
        seed=123456,
        identity_seed=777777,
        final_provider_seed=123456,
        model="krea-2-turbo",
        width=1024,
        height=1280,
        image_action="generate_new",
        metadata_json={
            "expected_subject_count": 1,
            "route_action": "generate_new",
            "visual_requirements": visual_requirements,
            "resolved_requested_framing": "full_body",
            "full_body_required": True,
            "identity_descriptor": {"face": "stable fictional face"},
        },
    )
    session.add(job)
    session.commit()
    return job


def _qa_result(*, passed: bool, reason_codes=None):
    result = GeneratedImageQAResult(
        passed=passed,
        person_count=1,
        face_count=1,
        second_person_visible=False,
        duplicate_subject_visible=False,
        reflected_person_visible=False,
        background_person_visible=False,
        selfie_detected=False,
        mirror_selfie_detected=True,
        confidence="high",
        reason_codes=list(reason_codes or []),
        model="test-vision",
        requested_nudity_visible=True,
        requested_scene_visible=True,
        framing_matches_request=passed,
        identity_consistency_reasonable=True,
        requested_full_body_visible=passed,
        head_inside_frame=True,
        feet_inside_frame=passed,
        body_not_cropped=passed,
    )
    result.camera_mode_matches_request = True
    result.camera_source_geometry_consistent = True
    result.third_person_viewpoint_detected = False
    result.natural_capture_plausible = True
    result.looks_like_id_photo = False
    return result


def _anatomy_pass():
    result = GeneratedImageQAResult(
        passed=True,
        person_count=None,
        face_count=None,
        second_person_visible=False,
        duplicate_subject_visible=False,
        reflected_person_visible=False,
        background_person_visible=False,
        selfie_detected=False,
        mirror_selfie_detected=False,
        confidence="high",
        reason_codes=[],
        model="consensus:test-a+test-b",
        anatomy_visible_enough_to_assess=True,
        anatomy_consistent_with_profile=True,
        contradictory_sex_characteristics=False,
        malformed_anatomy=False,
        implausible_anatomy=False,
        duplicated_anatomy_parts=False,
        missing_expected_parts_when_visible=False,
        ambiguous_anatomy=False,
    )
    result.consensus_passed = True
    result.qa_passes = [
        {"model": "test-a", "passed": True, "confidence": "high", "reason_codes": []},
        {"model": "test-b", "passed": True, "confidence": "high", "reason_codes": []},
    ]
    return result


class _Telegram:
    def __init__(self):
        self.photos = []
        self.texts = []

    async def send_photo_bytes(self, *args, **kwargs):
        self.photos.append((args, kwargs))
        return 987

    async def send_text(self, *args, **kwargs):
        self.texts.append((args, kwargs))
        return 988


class _KreaCropThenPassClient:
    def __init__(self):
        self.calls = []

    async def available_image_models(self):
        return {"krea-2-turbo", "seedream-v5-lite", "lustify-sdxl", "lustify-v8", "venice-sd35", "z-image-turbo"}

    async def generate(self, prompt, negative_prompt, *, width, height, seed, model):
        self.calls.append({"model": model, "prompt": prompt, "seed": seed})
        return ImageGenerationResponse(
            image_bytes=_png_bytes(100 + len(self.calls)),
            mime_type="image/png",
            request_id=f"krea-{len(self.calls)}",
            model=model,
            width=width,
            height=height,
            latency_seconds=0.01,
            response_type="binary",
            metadata={"seed_used": seed, "seed_fallback_used": False, "payload_profile": "test"},
        )


class _KreaProviderFailThenSeedreamClient(_KreaCropThenPassClient):
    async def generate(self, prompt, negative_prompt, *, width, height, seed, model):
        self.calls.append({"model": model, "prompt": prompt, "seed": seed})
        if model == "krea-2-turbo":
            raise ImageValidationError("model temporarily unavailable")
        return ImageGenerationResponse(
            image_bytes=_png_bytes(180),
            mime_type="image/png",
            request_id="seedream-ok",
            model=model,
            width=width,
            height=height,
            latency_seconds=0.01,
            response_type="binary",
            metadata={"seed_used": seed, "seed_fallback_used": False, "payload_profile": "test"},
        )


def test_worker_retries_krea_with_same_seed_and_identity_safe_correction_then_delivers(monkeypatch):
    import app.services.image_generation_service as service

    async def run():
        session = _session()
        user = User(telegram_id=901)
        session.add(user)
        session.flush()
        job = _job(session, user)
        client = _KreaCropThenPassClient()
        telegram = _Telegram()
        qa_calls = 0

        async def qa(*args, **kwargs):
            nonlocal qa_calls
            qa_calls += 1
            if qa_calls == 1:
                return _qa_result(
                    passed=False,
                    reason_codes=["framing_mismatch", "missing_feet", "cropped_body"],
                )
            return _qa_result(passed=True)

        async def anatomy(*args, **kwargs):
            return _anatomy_pass()

        monkeypatch.setattr(service, "get_settings", _settings)
        monkeypatch.setattr(service, "evaluate_adult_anatomy_image", anatomy)
        monkeypatch.setattr(
            service.GeneratedMediaArchiveService,
            "archive_image",
            lambda *args, **kwargs: asyncio.sleep(0, result=False),
        )

        result = await service.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
            generated_image_qa_evaluator=qa,
        )

        assert result.status == "sent"
        assert [call["model"] for call in client.calls] == ["krea-2-turbo", "krea-2-turbo"]
        assert client.calls[0]["seed"] == client.calls[1]["seed"] == 777777
        assert client.calls[0]["seed"] != job.seed
        assert result.metadata_json["stable_krea_identity_seed_source"] == 777777
        second_prompt = client.calls[1]["prompt"].lower()
        assert "strict partner-photo correction" in second_prompt
        assert "exact stored fictional identity" in second_prompt
        assert "facial geometry" in second_prompt
        assert "may change only framing" in second_prompt
        assert "head-to-feet" in second_prompt
        assert "floor below both feet" in second_prompt
        assert "70 percent" in second_prompt
        assert result.metadata_json["final_generation_model"] == "krea-2-turbo"
        assert result.metadata_json["effective_generation_attempt_plan"][:2] == [
            {"model": "krea-2-turbo", "correction_round": 0},
            {"model": "krea-2-turbo", "correction_round": 1},
        ]
        successful_attempt = result.metadata_json["provider_model_attempts"][-1]
        assert successful_attempt["correction_round"] == 1
        assert successful_attempt["generated_image_qa"]["passed"] is True
        assert result.metadata_json["anatomy_qa_passed"] is True
        assert len(telegram.photos) == 1
        assert not telegram.texts

    asyncio.run(run())


def test_provider_error_skips_same_model_correction_and_falls_back(monkeypatch):
    import app.services.image_generation_service as service

    async def run():
        session = _session()
        user = User(telegram_id=902)
        session.add(user)
        session.flush()
        job = _job(session, user)
        client = _KreaProviderFailThenSeedreamClient()
        telegram = _Telegram()

        async def anatomy(*args, **kwargs):
            return _anatomy_pass()

        monkeypatch.setattr(service, "get_settings", _settings)
        monkeypatch.setattr(service, "evaluate_adult_anatomy_image", anatomy)
        monkeypatch.setattr(
            service.GeneratedMediaArchiveService,
            "archive_image",
            lambda *args, **kwargs: asyncio.sleep(0, result=False),
        )

        result = await service.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
            generated_image_qa_evaluator=lambda *args, **kwargs: asyncio.sleep(
                0, result=_qa_result(passed=True)
            ),
        )

        assert result.status == "sent"
        assert [call["model"] for call in client.calls] == ["krea-2-turbo", "seedream-v5-lite"]
        assert result.metadata_json["final_generation_model"] == "seedream-v5-lite"
        assert result.metadata_json["fallback_model_used"] is True
        assert result.metadata_json["provider_model_attempts"][0]["error_code"] == "validation"
        assert len(telegram.photos) == 1

    asyncio.run(run())


def test_two_krea_quality_failures_fall_back_only_to_seedream(monkeypatch):
    import app.services.image_generation_service as service

    async def run():
        session = _session()
        user = User(telegram_id=903)
        session.add(user)
        session.flush()
        job = _job(session, user)
        client = _KreaCropThenPassClient()
        telegram = _Telegram()
        qa_calls = 0

        async def qa(*args, **kwargs):
            nonlocal qa_calls
            qa_calls += 1
            if qa_calls <= 2:
                return _qa_result(
                    passed=False,
                    reason_codes=["framing_mismatch", "missing_feet", "cropped_body"],
                )
            return _qa_result(passed=True)

        async def anatomy(*args, **kwargs):
            return _anatomy_pass()

        monkeypatch.setattr(service, "get_settings", _settings)
        monkeypatch.setattr(service, "evaluate_adult_anatomy_image", anatomy)
        monkeypatch.setattr(
            service.GeneratedMediaArchiveService,
            "archive_image",
            lambda *args, **kwargs: asyncio.sleep(0, result=False),
        )

        result = await service.process_job(
            session,
            job,
            image_client=client,
            telegram_service=telegram,
            generated_image_qa_evaluator=qa,
        )

        assert result.status == "sent"
        assert [call["model"] for call in client.calls] == [
            "krea-2-turbo",
            "krea-2-turbo",
            "seedream-v5-lite",
        ]
        assert client.calls[0]["seed"] == client.calls[1]["seed"] == 777777
        assert client.calls[2]["seed"] != client.calls[0]["seed"]
        assert result.metadata_json["final_generation_model"] == "seedream-v5-lite"
        assert result.metadata_json["fallback_model_used"] is True
        assert all(
            call["model"] not in {"lustify-sdxl", "lustify-v8", "venice-sd35", "z-image-turbo"}
            for call in client.calls
        )
        assert "exact stored fictional identity" in client.calls[2]["prompt"].lower()
        assert len(telegram.photos) == 1

    asyncio.run(run())
