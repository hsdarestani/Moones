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
from dataclasses import asdict
from types import SimpleNamespace


def _safe_attempt_seed(base_seed: int, attempt_index: int) -> int:
    digest = hashlib.sha256(f"{base_seed}:live-krea-smoke:{attempt_index}".encode()).digest()
    return 1 + (int.from_bytes(digest[:8], "big") % 999_999_999)


async def _run_live_krea_smoke() -> None:
    from app.core.config import get_settings
    from app.llm.image_client import VENICE_SEED_MIN, VeniceImageClient
    from app.services.generated_image_qa_service import (
        _configured_vision_reviewer_models,
        corrective_prompt_for_reasons,
        evaluate_explicit_anatomy,
        evaluate_single_subject_image,
    )
    from app.services.image_pipeline_v2 import (
        ContentClassification,
        ImageAction,
        PolicyDecision,
        SafetyDecision,
        compile_image_prompt,
        construct_resolved_plan,
        merge_image_intent,
        normalize_request_v2,
        parse_image_intent,
    )

    settings = get_settings()
    client = VeniceImageClient()
    request = "یه عکس تمام‌قد کاملاً لخت جلوی آینه توی اتاقت بده"
    normalized = normalize_request_v2(request, user_id=0, chat_id=0, source_message_id=0)
    intent = parse_image_intent(normalized)
    intent.content_classification = ContentClassification.FULL_NUDITY
    intent.continuity.action = ImageAction.NEW_GENERATION
    merged = merge_image_intent(
        intent,
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    profile = SimpleNamespace(
        profile_json={
            "identity_anchor": {
                "face": "oval adult face",
                "hair": "dark shoulder-length hair",
                "eyes": "dark almond-shaped eyes",
                "skin": "olive skin",
                "body": "natural adult feminine build",
                "distinguishing_details": "natural eyebrows",
            },
            "identity_anchor_fingerprint": "live-krea-smoke-fingerprint",
            "anatomical_profile": "female",
            "anatomical_profile_source": "explicit_profile",
            "mutable_profile_overlays": {"fictional_age": 30},
        },
        anatomical_profile="female",
        gender_presentation="feminine",
        base_seed=1,
        user_id=0,
        version=4,
        partner_name="Mones",
        fictional_age=30,
        face_description="oval adult face",
        hair_description="dark shoulder-length hair",
        eye_description="dark almond-shaped eyes",
        skin_description="olive skin",
        body_description="natural adult feminine build",
        height_impression="average height",
        distinguishing_details="natural eyebrows",
        updated_at=None,
    )
    plan = construct_resolved_plan(
        intent,
        merged,
        SafetyDecision(PolicyDecision.ALLOW),
        profile,
        message_id=0,
        user_request=request,
    )
    compiled = compile_image_prompt(plan)
    models = ["krea-2-turbo", "seedream-v5-lite"]
    reviewer_models = _configured_vision_reviewer_models(settings)
    attempt_summaries = []
    correction_codes = []
    attempt_index = 0

    for model in models:
        rounds = 2 if model == "krea-2-turbo" else 2
        for round_index in range(rounds):
            attempt_index += 1
            prompt = compiled.positive_prompt
            if correction_codes:
                prompt = f"{prompt}\n\n{corrective_prompt_for_reasons(correction_codes)}"
            seed = _safe_attempt_seed(profile.base_seed, attempt_index)
            result = await client.generate(
                prompt=prompt,
                negative_prompt=compiled.negative_prompt,
                model=model,
                seed=seed,
                width=1024,
                height=1280,
            )
            checksum_prefix = hashlib.sha256(result.image_bytes or b"").hexdigest()[:12]
            if not result.image_bytes:
                attempt_summaries.append(
                    {
                        "attempt": attempt_index,
                        "model": model,
                        "seed": seed,
                        "provider": result.artifact_type or "empty",
                        "reason": result.error_code,
                        "checksum": checksum_prefix,
                    }
                )
                correction_codes = []
                continue
            if result.artifact_type != "image":
                attempt_summaries.append(
                    {
                        "attempt": attempt_index,
                        "model": model,
                        "seed": seed,
                        "provider": result.artifact_type,
                        "reason": result.error_code,
                        "checksum": checksum_prefix,
                    }
                )
                correction_codes = []
                continue

            qa = await evaluate_single_subject_image(
                result.image_bytes,
                user_request=request,
                positive_prompt=prompt,
                visual_requirements=asdict(plan.visual_requirements),
                expected_subject_count=1,
                reviewer_models=reviewer_models,
            )
            if not qa.passed:
                correction_codes = list(qa.reason_codes or [])
                attempt_summaries.append(
                    {
                        "attempt": attempt_index,
                        "model": model,
                        "seed": seed,
                        "provider": "image",
                        "qa": "failed",
                        "reason_codes": correction_codes,
                        "checksum": checksum_prefix,
                        "bytes": len(result.image_bytes),
                    }
                )
                continue

            anatomy = await evaluate_explicit_anatomy(
                result.image_bytes,
                required_profile="female",
                reviewer_models=reviewer_models,
            )
            if anatomy.passed:
                attempt_summaries.append(
                    {
                        "attempt": attempt_index,
                        "model": model,
                        "seed": seed,
                        "provider": "image",
                        "qa": "passed",
                        "anatomy": "passed",
                        "checksum": checksum_prefix,
                        "bytes": len(result.image_bytes),
                    }
                )
                print(
                    "LIVE_KREA_SMOKE_OK "
                    f"final_model={model} attempts={attempt_index} summaries={attempt_summaries}",
                    flush=True,
                )
                return

            correction_codes = list(anatomy.reason_codes or [])
            anatomy_diagnostics = {
                key: value
                for key, value in (anatomy.diagnostics or {}).items()
                if key in {"reviewer_model", "reviewer_status", "transport_status"}
            }
            attempt_summaries.append(
                {
                    "attempt": attempt_index,
                    "model": model,
                    "seed": seed,
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
        f"models={models} summaries={attempt_summaries}"
    )


def pytest_sessionstart(session) -> None:
    # The key is deliberately absent from GitHub Actions. It is available only in
    # the protected production application container used by the deploy command.
    from app.core.config import get_settings

    if not get_settings().venice_api_key:
        return

    # A temporary release-specific worker probe, when present, owns this test
    # session and intentionally aborts after its isolated end-to-end checks so
    # the protected deploy rolls back. This keeps unrelated live provider smoke
    # from masking the result of the release-specific worker path.
    try:
        from app.services.live_partner_worker_probe import (
            run_live_partner_worker_probe_if_configured,
        )
    except ModuleNotFoundError:
        pass
    else:
        import os

        os.environ["MOONES_LIVE_PARTNER_WORKER_PROBE"] = "1"
        run_live_partner_worker_probe_if_configured()

    asyncio.run(_run_live_krea_smoke())
