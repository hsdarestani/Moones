"""Temporary production-only release gate for the Krea adult-image path.

The GitHub runner has no Venice key, so this hook is a no-op there. During the
protected production deploy, pytest runs inside the application container with
its real environment. The hook then generates the exact previously failing
request, runs the same visual and anatomy QA, and aborts deployment before the
application restart unless Krea produces a deliverable result.

No prompt, image bytes, or intimate image description is logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace


def _safe_attempt_seed(base_seed: int, attempt_index: int) -> int:
    digest = hashlib.sha256(f"{base_seed}:live-krea-smoke:{attempt_index}".encode()).digest()
    return 1 + (int.from_bytes(digest[:8], "big") % 999_999_999)


async def _run_live_krea_smoke() -> None:
    from app.core.config import get_settings
    from app.llm.image_client import VENICE_SEED_MIN, VeniceImageClient
    from app.llm.vision_client import analyze_image_bytes_with_venice
    from app.services.generated_image_qa_service import (
        ADULT_ANATOMY_PROFILE_QA_PROMPT,
        ADULT_ANATOMY_QA_SCHEMA,
        ADULT_ANATOMY_STRUCTURE_QA_PROMPT,
        corrective_prompt_for_reasons,
        evaluate_adult_anatomy_image,
        evaluate_single_subject_image,
    )
    from app.services.image_pipeline_v2 import (
        PolicyDecision,
        SafetyDecision,
        compile_image_prompt,
        construct_resolved_plan,
        ensure_visual_profile_v2,
        merge_image_intent,
        normalize_request_v2,
        parse_image_intent,
    )
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    settings = get_settings()
    if not settings.venice_api_key:
        print("LIVE_KREA_SMOKE skipped=no_provider_key", flush=True)
        return

    async def diagnose_anatomy_reviewers(image_bytes: bytes, anatomical_profile: str) -> list[dict]:
        fallback = settings.vision_fallback_model or settings.vision_model
        review_plan = [
            (settings.vision_model, ADULT_ANATOMY_PROFILE_QA_PROMPT, "profile"),
            (fallback, ADULT_ANATOMY_STRUCTURE_QA_PROMPT, "structure"),
        ]
        summaries: list[dict] = []
        for review_model, review_prompt, phase in review_plan:
            prompt = (
                review_prompt
                + "\nSchema: "
                + ADULT_ANATOMY_QA_SCHEMA
                + "\nRequirements: "
                + json.dumps({"anatomical_profile": anatomical_profile}, sort_keys=True)
            )
            try:
                payload = await analyze_image_bytes_with_venice(
                    image_bytes,
                    prompt=prompt,
                    model=review_model,
                )
            except Exception as exc:
                detail = " ".join(str(exc).split())
                if "base64" in detail.lower() or len(detail) > 240:
                    detail = detail[:240]
                summaries.append(
                    {
                        "model": review_model,
                        "phase": phase,
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_detail": detail,
                    }
                )
                continue
            summaries.append(
                {
                    "model": review_model,
                    "phase": phase,
                    "status": "parsed",
                    "keys": sorted(str(key) for key in payload.keys()),
                    "confidence": payload.get("confidence"),
                    "reason_codes": [str(code) for code in (payload.get("reason_codes") or [])],
                }
            )
        return summaries

    model = "krea-2-turbo"
    client = VeniceImageClient()
    available = await client.available_image_models(ttl_seconds=30)
    if available is not None and model not in available:
        raise RuntimeError(
            "LIVE_KREA_SMOKE_FAILED stage=model_discovery "
            f"model={model} available_krea={sorted(x for x in available if 'krea' in x.lower())}"
        )

    class DummyDB:
        def flush(self):
            return None

    request = "یه عکس تمام‌قد کاملاً لخت جلوی آینه توی اتاقت بده"
    user = SimpleNamespace(partner_gender="دختر")
    profile = SimpleNamespace(
        profile_json={},
        anatomical_profile=None,
        gender_presentation="feminine",
        base_seed=VENICE_SEED_MIN,
        user_id=1,
        version=3,
        partner_name="مونس",
        fictional_age=25,
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
    intent = parse_image_intent(normalize_request_v2(request))
    merged = merge_image_intent(intent, recent_context=[], memory_context=[], routine_context={})
    plan = construct_resolved_plan(
        intent,
        merged,
        SafetyDecision(PolicyDecision.ALLOW),
        profile,
        message_id=191,
        user_request=request,
    )
    compiled = compile_image_prompt(plan)
    requirements = asdict(plan.visual_requirements)
    requirements["environment_visibility_required"] = True
    requirements.setdefault("visibility_targets", {})["environment_visible"] = True
    requirements.setdefault("must_satisfy", {})["required_scene_elements"] = [
        "private_indoor",
        "mirror",
    ]

    expected_subject_count = int(plan.composition.get("expected_subject_count", 1))
    base_seed = int(plan.seed_strategy["final_provider_seed"])
    correction_codes: list[str] = []
    attempt_summaries: list[dict] = []

    for attempt_index in range(2):
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
        seed = _safe_attempt_seed(base_seed, attempt_index)
        result = await client.generate(
            prompt,
            compiled.negative_prompt,
            width=int(compiled.provider_parameters["width"]),
            height=int(compiled.provider_parameters["height"]),
            seed=seed,
            model=model,
        )
        checksum_prefix = hashlib.sha256(result.image_bytes).hexdigest()[:12]
        moderation = detect_provider_error_screen(result.image_bytes)
        if moderation.is_error_screen:
            attempt_summaries.append(
                {
                    "attempt": attempt_index + 1,
                    "provider": "moderation_artifact",
                    "reason": moderation.reason,
                    "checksum": checksum_prefix,
                }
            )
            correction_codes = ["requested_nudity_missing"]
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
        qa_codes = list(qa.reason_codes or [])
        visual_contract_passed = bool(
            qa.passed
            and qa.requested_nudity_visible is True
            and qa.requested_full_body_visible is True
            and qa.head_inside_frame is True
            and qa.feet_inside_frame is True
            and qa.body_not_cropped is True
            and qa.requested_scene_visible is True
            and qa.mirror_selfie_detected is True
        )
        if not visual_contract_passed:
            if qa.requested_nudity_visible is not True:
                qa_codes.append("requested_nudity_missing")
            if qa.requested_full_body_visible is not True:
                qa_codes.append("missing_full_body")
            if qa.feet_inside_frame is not True:
                qa_codes.append("missing_feet")
            if qa.body_not_cropped is not True:
                qa_codes.append("cropped_body")
            if qa.requested_scene_visible is not True:
                qa_codes.append("requested_scene_not_visible")
            if qa.mirror_selfie_detected is not True:
                qa_codes.append("camera_mode_mismatch")
            correction_codes = list(dict.fromkeys(qa_codes))
            attempt_summaries.append(
                {
                    "attempt": attempt_index + 1,
                    "provider": "image",
                    "qa": "failed",
                    "reason_codes": correction_codes,
                    "checksum": checksum_prefix,
                    "bytes": len(result.image_bytes),
                }
            )
            continue

        anatomy = await evaluate_adult_anatomy_image(
            result.image_bytes,
            anatomical_profile=requirements.get("anatomical_profile"),
            user_id="release-smoke",
            job_id="release-smoke",
            request_chain_id="release-smoke",
        )
        if anatomy.passed and getattr(anatomy, "consensus_passed", False):
            attempt_summaries.append(
                {
                    "attempt": attempt_index + 1,
                    "provider": "image",
                    "qa": "passed",
                    "anatomy": "passed",
                    "checksum": checksum_prefix,
                    "bytes": len(result.image_bytes),
                }
            )
            print(
                "LIVE_KREA_SMOKE_OK "
                f"model={model} attempts={attempt_index + 1} summaries={attempt_summaries}",
                flush=True,
            )
            return

        correction_codes = list(dict.fromkeys(anatomy.reason_codes or ["anatomy_qa_disagreement"]))
        anatomy_diagnostics = await diagnose_anatomy_reviewers(
            result.image_bytes,
            requirements.get("anatomical_profile"),
        )
        attempt_summaries.append(
            {
                "attempt": attempt_index + 1,
                "provider": "image",
                "qa": "passed",
                "anatomy": "failed",
                "reason_codes": correction_codes,
                "anatomy_diagnostics": anatomy_diagnostics,
                "checksum": checksum_prefix,
                "bytes": len(result.image_bytes),
            }
        )

    raise RuntimeError(
        "LIVE_KREA_SMOKE_FAILED stage=delivery_contract "
        f"model={model} summaries={attempt_summaries}"
    )


def pytest_sessionstart(session) -> None:
    # The key is deliberately absent from GitHub Actions. It is available only in
    # the protected production application container used by the deploy command.
    from app.core.config import get_settings

    if not get_settings().venice_api_key:
        return
    asyncio.run(_run_live_krea_smoke())
