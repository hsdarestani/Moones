from __future__ import annotations

import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.llm.image_client import VeniceImageClient
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
from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base_service


_ALLOWED_MODELS = {"krea-2-turbo", "seedream-v5-lite"}
_IDENTITY_SEED = 731492611
_IDENTITY_DESCRIPTOR = {
    "face": "same fictional adult woman with an oval face, dark brown almond-shaped eyes, a straight refined nose, balanced lips, and a softly defined jaw",
    "hair": "shoulder-length dark brown hair with a natural slight wave",
    "skin_tone": "medium-light olive skin",
    "body_build": "natural slim-to-average adult build",
}


class _TelegramProbe:
    def __init__(self) -> None:
        self.photos: list[dict] = []
        self.texts: list[tuple] = []

    async def send_photo_bytes(self, chat_id, image_bytes, **kwargs):
        self.photos.append({"chat_id": chat_id, "bytes": len(image_bytes or b""), "kwargs": kwargs})
        return 910001 + len(self.photos)

    async def send_text(self, *args, **kwargs):
        self.texts.append((args, kwargs))
        return 920001 + len(self.texts)


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
    return sessionmaker(bind=engine)(), engine


def _job(session, user, *, key: str, request: str, prompt: str, requirements: dict, seed: int):
    job = ImageGenerationJob(
        idempotency_key=f"live-worker-probe:{key}",
        correlation_id=f"live-worker-probe:{key}",
        user_id=user.id,
        chat_id=777001,
        status="processing",
        attempt_count=1,
        max_attempts=3,
        content_mode="normal",
        user_request=request,
        prompt=prompt,
        negative_prompt=(
            "extra people, second person, duplicate person, collage, split panel, repeated subject, "
            "watermark, text, logo, cropped body, wrong scene, conspicuous unrelated foreground prop"
        ),
        seed=seed,
        identity_seed=_IDENTITY_SEED,
        final_provider_seed=seed,
        model="krea-2-turbo",
        width=1024,
        height=1280,
        image_action="generate_new",
        metadata_json={
            "expected_subject_count": 1,
            "route_action": "generate_new",
            "content_classification": "normal",
            "identity_descriptor": dict(_IDENTITY_DESCRIPTOR),
            "identity_seed": _IDENTITY_SEED,
            "identity_fingerprint": "live-worker-probe-stable-fictional-identity",
            "visual_requirements": requirements,
            "photo_contract": dict(requirements.get("photo_contract") or {}),
            "resolved_requested_framing": requirements.get("framing_requirement"),
            "full_body_required": bool(requirements.get("full_body_visible")),
            "head_visible_required": bool(requirements.get("head_visible")),
            "feet_visible_required": bool(requirements.get("feet_visible")),
            "body_not_cropped_required": bool(requirements.get("body_not_cropped")),
        },
    )
    session.add(job)
    session.commit()
    return job


def _bookstore_requirements() -> dict:
    contract = {
        "request_type": "new_photo",
        "primary_subject": "partner",
        "partner_visible": True,
        "face_visible": True,
        "camera_mode": "casual_phone_photo",
        "camera_explicit_current_request": False,
        "framing": "natural_medium_or_medium_wide",
        "framing_explicit_current_request": False,
        "visible_objects": ["book", "bookshelves"],
        "held_objects": ["book"],
        "natural_capture_required": True,
        "identity_visibility_scope": "full",
        "expected_human_subject_count": 1,
        "identity_consistency_required": True,
    }
    return {
        "partner_visible": True,
        "face_visible_required": True,
        "camera_mode": "casual_phone_photo",
        "natural_capture_required": True,
        "identity_visibility_scope": "full",
        "required_objects": ["book", "bookshelves"],
        "environment_visibility_required": True,
        "wardrobe_requested": True,
        "wardrobe_visibility_required": True,
        "framing_requirement": "natural_medium_or_medium_wide",
        "full_body_visible": False,
        "head_visible": True,
        "feet_visible": False,
        "body_not_cropped": False,
        "explicit_nudity_requested": False,
        "anatomy_qa_required": False,
        "style_targets": {"wardrobe": "oversized gray sweater"},
        "must_satisfy": {
            "required_scene_elements": ["old bookstore", "bookshelves"],
            "required_visible_objects": ["book", "bookshelves"],
        },
        "photo_contract": contract,
    }


def _rooftop_requirements() -> dict:
    contract = {
        "request_type": "new_photo",
        "primary_subject": "partner",
        "partner_visible": True,
        "face_visible": True,
        "camera_mode": "tripod_timer",
        "camera_explicit_current_request": False,
        "framing": "full_body",
        "framing_explicit_current_request": True,
        "visible_objects": ["city lights", "rooftop"],
        "held_objects": [],
        "natural_capture_required": True,
        "identity_visibility_scope": "full",
        "expected_human_subject_count": 1,
        "identity_consistency_required": True,
    }
    return {
        "partner_visible": True,
        "face_visible_required": True,
        "camera_mode": "tripod_timer",
        "natural_capture_required": True,
        "identity_visibility_scope": "full",
        "required_objects": ["city lights"],
        "environment_visibility_required": True,
        "wardrobe_requested": True,
        "wardrobe_visibility_required": True,
        "framing_requirement": "full_body",
        "full_body_visible": True,
        "head_visible": True,
        "feet_visible": True,
        "body_not_cropped": True,
        "explicit_nudity_requested": False,
        "anatomy_qa_required": False,
        "style_targets": {"wardrobe": "formal black outfit"},
        "must_satisfy": {
            "required_scene_elements": ["rooftop at night", "city lights in the background"],
            "required_visible_objects": ["city lights"],
        },
        "photo_contract": contract,
    }


async def _run_one(session, user, client, *, label: str, request: str, prompt: str, requirements: dict, seed: int):
    telegram = _TelegramProbe()
    job = _job(
        session,
        user,
        key=label,
        request=request,
        prompt=prompt,
        requirements=requirements,
        seed=seed,
    )
    result = await runtime.process_job(
        session,
        job,
        image_client=client,
        telegram_service=telegram,
        generated_image_qa_evaluator=None,
    )
    session.commit()

    meta = dict(result.metadata_json or {})
    attempts = list(meta.get("provider_model_attempts") or [])
    models = [str(item.get("model") or "") for item in attempts]
    if result.status != "sent":
        raise RuntimeError(
            "LIVE_PARTNER_WORKER_FAILED "
            + json.dumps(
                {
                    "label": label,
                    "status": result.status,
                    "error_code": result.error_code,
                    "final_qa_reason_codes": meta.get("final_qa_reason_codes"),
                    "attempts": [
                        {
                            "model": item.get("model"),
                            "correction_round": item.get("correction_round"),
                            "error_code": item.get("error_code"),
                            "qa": (item.get("generated_image_qa") or {}).get("reason_codes"),
                            "raw_qa": (item.get("generated_image_qa") or {}).get("raw_provider_reason_codes"),
                        }
                        for item in attempts
                    ],
                },
                ensure_ascii=False,
            )
        )
    if len(telegram.photos) != 1 or telegram.texts:
        raise RuntimeError(f"LIVE_PARTNER_WORKER_DELIVERY_CONTRACT_FAILED label={label}")
    if not models or any(model not in _ALLOWED_MODELS for model in models):
        raise RuntimeError(f"LIVE_PARTNER_WORKER_MODEL_POLICY_FAILED label={label} models={models}")
    plan = list(meta.get("effective_generation_attempt_plan") or [])
    if [item.get("model") for item in plan] != [
        "krea-2-turbo",
        "krea-2-turbo",
        "seedream-v5-lite",
        "seedream-v5-lite",
    ]:
        raise RuntimeError(f"LIVE_PARTNER_WORKER_ATTEMPT_PLAN_FAILED label={label} plan={plan}")

    summary = {
        "label": label,
        "status": result.status,
        "final_model": meta.get("final_generation_model"),
        "camera_mode": requirements.get("camera_mode"),
        "attempt_count": len(attempts),
        "attempts": [
            {
                "model": item.get("model"),
                "correction_round": item.get("correction_round"),
                "qa_passed": (item.get("generated_image_qa") or {}).get("passed"),
                "qa_reason_codes": (item.get("generated_image_qa") or {}).get("reason_codes"),
                "raw_qa_reason_codes": (item.get("generated_image_qa") or {}).get("raw_provider_reason_codes"),
            }
            for item in attempts
        ],
    }
    print("LIVE_PARTNER_WORKER_CASE_OK " + json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


async def _run() -> None:
    session, engine = _session()
    user = User(telegram_id=990001)
    session.add(user)
    session.flush()
    client = VeniceImageClient()

    original_archive = base_service.GeneratedMediaArchiveService.archive_image
    original_record = base_service.record_media_delivery

    async def no_archive(*args, **kwargs):
        return False

    base_service.GeneratedMediaArchiveService.archive_image = no_archive
    base_service.record_media_delivery = lambda *args, **kwargs: None
    try:
        results = []
        results.append(
            await _run_one(
                session,
                user,
                client,
                label="bookstore",
                request="یه عکس از خودت بده که وسط یه کتاب‌فروشی قدیمی بین قفسه‌ها ایستادی، یه پلیور طوسی گشاد پوشیدی و داری یه کتاب رو ورق می‌زنی.",
                prompt=(
                    "Exactly one recurring fictional adult woman, preserving the exact same stored fictional identity. "
                    "A believable spontaneous personal phone photo in an old bookstore, standing naturally between tall bookshelves, "
                    "wearing an oversized gray sweater and actively flipping through an open book held naturally in her hands. "
                    "Her recognizable face and the bookstore environment are clearly visible. Medium-wide natural composition, no selfie requirement. "
                    "Photorealistic, ordinary real-life phone-camera perspective, natural skin texture and posture."
                ),
                requirements=_bookstore_requirements(),
                seed=44120931,
            )
        )
        results.append(
            await _run_one(
                session,
                user,
                client,
                label="rooftop",
                request="حالا یه عکس تمام‌قد از خودت روی پشت‌بوم یه ساختمون شب، باد موهاتو به‌هم زده، لباس مشکی رسمی پوشیدی و چراغ‌های شهر پشت سرت معلومه.",
                prompt=(
                    "Exactly one recurring fictional adult woman, preserving the exact same stored fictional identity. "
                    "A believable full-body timer photo on a building rooftop at night. She wears a formal black outfit; wind naturally moves her dark hair; "
                    "city lights are clearly visible behind her. Complete figure from head through both feet fully inside frame, visible floor beneath both feet, "
                    "camera far enough away, no crop, no mirror, no handheld selfie, no visible photographer. Photorealistic natural night photography."
                ),
                requirements=_rooftop_requirements(),
                seed=88271643,
            )
        )
        print("LIVE_PARTNER_WORKER_SMOKE_OK " + json.dumps(results, ensure_ascii=False), flush=True)
    finally:
        base_service.GeneratedMediaArchiveService.archive_image = original_archive
        base_service.record_media_delivery = original_record
        session.close()
        engine.dispose()


def run_live_partner_worker_probe_if_configured() -> None:
    if str(__import__("os").environ.get("MOONES_LIVE_PARTNER_WORKER_PROBE", "")).strip() != "1":
        return
    asyncio.run(_run())
    raise RuntimeError("LIVE_PARTNER_WORKER_PROBE_COMPLETE_ROLLBACK")
