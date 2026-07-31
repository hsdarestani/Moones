from __future__ import annotations

"""Temporary one-off protected probe for arbitrary non-adult partner images.

No user job, Telegram delivery, wallet, prompt, image bytes, or provider secret is
persisted/logged. This module will be removed from main immediately after the
probe. A successful probe deliberately raises at the end so the production
deploy wrapper rolls back to the already-clean release.
"""

import asyncio
import hashlib
from dataclasses import asdict
from types import SimpleNamespace


def _seed(base: int, label: str, index: int) -> int:
    digest = hashlib.sha256(f"{base}:{label}:{index}".encode()).digest()
    return 1 + (int.from_bytes(digest[:8], "big") % 999_999_999)


async def _probe_one(label: str, request: str, profile) -> dict:
    from app.core.config import get_settings
    from app.llm.image_client import VeniceImageClient
    from app.services.generated_image_qa_service import (
        corrective_prompt_for_reasons,
        evaluate_single_subject_image,
    )
    from app.services.image_generation_runtime import (
        _runtime_attempt_plan,
        _runtime_model_plan,
    )
    from app.services.image_pipeline_v2 import (
        PolicyDecision,
        SafetyDecision,
        compile_image_prompt,
        construct_resolved_plan,
        merge_image_intent,
        normalize_request_v2,
        parse_image_intent,
    )
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    settings = get_settings()
    client = VeniceImageClient()
    intent = parse_image_intent(normalize_request_v2(request))
    merged = merge_image_intent(intent, recent_context=[], memory_context=[], routine_context={})
    plan = construct_resolved_plan(
        intent,
        merged,
        SafetyDecision(PolicyDecision.ALLOW),
        profile,
        message_id=7100 if label == "bookstore" else 7101,
        user_request=request,
    )
    compiled = compile_image_prompt(plan)
    requirements = asdict(plan.visual_requirements)
    expected_subject_count = int(plan.composition.get("expected_subject_count", 1))
    base_seed = int(plan.seed_strategy["identity_seed"])

    model_plan = _runtime_model_plan(
        settings,
        "krea-2-turbo",
        adult_generation=False,
        identity_locked_generation=True,
    )
    attempt_plan = _runtime_attempt_plan(
        model_plan,
        adult_generation=False,
        identity_locked_generation=True,
        max_attempts=4,
    )
    expected_models = [
        "krea-2-turbo",
        "krea-2-turbo",
        "seedream-v5-lite",
        "seedream-v5-lite",
    ]
    if [model for model, _ in attempt_plan] != expected_models:
        raise RuntimeError(
            f"LIVE_NONADULT_CONTEXT_FAILED label={label} stage=runtime_plan plan={attempt_plan}"
        )

    available = await client.available_image_models(ttl_seconds=30)
    if available is not None:
        missing = [
            model
            for model in {"krea-2-turbo", "seedream-v5-lite"}
            if model not in available
        ]
        if missing:
            raise RuntimeError(
                f"LIVE_NONADULT_CONTEXT_FAILED label={label} stage=model_discovery missing={missing}"
            )

    correction_codes: list[str] = []
    last_quality_model: str | None = None
    summaries: list[dict] = []
    stable_krea_seed = _seed(base_seed, "partner-krea", 0)

    for attempt_index, (model, correction_round) in enumerate(attempt_plan):
        if model == "krea-2-turbo" and correction_round and last_quality_model != model:
            continue

        prompt = compiled.positive_prompt
        if correction_codes:
            prompt += corrective_prompt_for_reasons(
                correction_codes,
                expected_subject_count=expected_subject_count,
                expected_interaction=plan.composition.get("interaction"),
                secondary_subject_role=plan.composition.get("secondary_subject_role"),
                identity_requirements=plan.identity.get("descriptor"),
                photo_contract=requirements.get("photo_contract") or {},
            )

        seed = (
            stable_krea_seed
            if model == "krea-2-turbo"
            else _seed(base_seed, f"{label}-seedream", attempt_index)
        )
        try:
            result = await client.generate(
                prompt,
                compiled.negative_prompt,
                width=int(compiled.provider_parameters["width"]),
                height=int(compiled.provider_parameters["height"]),
                seed=seed,
                model=model,
            )
        except Exception as exc:
            summaries.append(
                {
                    "attempt": attempt_index + 1,
                    "model": model,
                    "provider": "error",
                    "error_type": type(exc).__name__,
                }
            )
            correction_codes = []
            last_quality_model = None
            continue

        checksum = hashlib.sha256(result.image_bytes).hexdigest()[:12]
        moderation = detect_provider_error_screen(result.image_bytes)
        if moderation.is_error_screen:
            summaries.append(
                {
                    "attempt": attempt_index + 1,
                    "model": model,
                    "provider": "moderation_artifact",
                    "checksum": checksum,
                }
            )
            correction_codes = []
            last_quality_model = None
            continue

        qa = await evaluate_single_subject_image(
            result.image_bytes,
            expected_subject_count=expected_subject_count,
            expected_interaction=plan.composition.get("interaction"),
            selfie_allowed=True,
            mirror_allowed=True,
            visual_requirements=requirements,
            previous_metadata=None,
        )
        if qa.passed:
            summaries.append(
                {
                    "attempt": attempt_index + 1,
                    "model": model,
                    "provider": "image",
                    "qa": "passed",
                    "checksum": checksum,
                    "bytes": len(result.image_bytes),
                }
            )
            return {
                "label": label,
                "final_model": model,
                "attempts": attempt_index + 1,
                "summaries": summaries,
            }

        correction_codes = list(
            dict.fromkeys(qa.reason_codes or ["visual_contract_failed"])
        )
        last_quality_model = model
        summaries.append(
            {
                "attempt": attempt_index + 1,
                "model": model,
                "provider": "image",
                "qa": "failed",
                "reason_codes": correction_codes,
                "checksum": checksum,
                "bytes": len(result.image_bytes),
            }
        )

    raise RuntimeError(
        f"LIVE_NONADULT_CONTEXT_FAILED label={label} stage=delivery_contract summaries={summaries}"
    )


async def _run() -> None:
    from app.core.config import get_settings
    from app.llm.image_client import VENICE_SEED_MIN
    from app.services.image_pipeline_v2 import ensure_visual_profile_v2

    if not get_settings().venice_api_key:
        return

    class DummyDB:
        def flush(self):
            return None

    user = SimpleNamespace(
        partner_gender="دختر",
        partner_name="مهناز",
        partner_age_range="18-20",
    )
    profile = SimpleNamespace(
        profile_json={},
        anatomical_profile="female",
        gender_presentation="feminine",
        base_seed=VENICE_SEED_MIN,
        user_id=1,
        version=3,
        partner_name="مهناز",
        fictional_age=18,
        face_description="oval face, softly defined jawline, natural facial proportions",
        hair_description="dark shoulder-length hair with a natural hairline",
        eye_description="dark almond-shaped eyes and natural eyebrows",
        skin_description="olive skin with natural texture",
        body_description="average adult feminine build with natural proportions",
        height_impression="average height",
        distinguishing_details="natural eyebrows",
        updated_at=None,
    )
    profile = ensure_visual_profile_v2(DummyDB(), user, profile)

    requests = [
        (
            "bookstore",
            "یه عکس از خودت بده که وسط یه کتاب‌فروشی قدیمی بین قفسه‌ها ایستادی، یه پلیور طوسی گشاد پوشیدی و داری یه کتاب رو ورق می‌زنی.",
        ),
        (
            "rooftop",
            "حالا یه عکس تمام‌قد از خودت روی پشت‌بوم یه ساختمون شب، باد موهاتو به‌هم زده، لباس مشکی رسمی پوشیدی و چراغ‌های شهر پشت سرت معلومه.",
        ),
    ]
    results = [await _probe_one(label, request, profile) for label, request in requests]
    print(f"LIVE_NONADULT_CONTEXT_SMOKE_OK results={results}", flush=True)
    raise RuntimeError("LIVE_NONADULT_CONTEXT_PROBE_COMPLETE_ROLLBACK")


def run_live_nonadult_context_probe_if_configured() -> None:
    asyncio.run(_run())
