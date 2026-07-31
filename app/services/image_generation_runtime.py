from __future__ import annotations

"""Runtime policy adapter for recurring-partner image generation.

The core worker stays reusable for legacy/object/pet routes. The production app
imports this adapter so any image that visibly represents the persistent partner
gets one scene-agnostic delivery contract:

    Krea base -> same-seed Krea QA correction -> Seedream fallback
    -> one final Seedream retry before refund.

The adapter also closes generic routing/acceptance boundaries:

* ordinary visible regions (hair, face, eyes, hands, arms, etc.) must never turn
  an otherwise NORMAL request into an adult/suggestive request;
* numeric edge ages from the persistent profile stay authoritative throughout the
  internal plan/compiled prompt, while only provider-bound text uses an equivalent
  unambiguously-adult visual age band;
* fresh explicit scenes do not inherit stale pose/activity/support/camera state;
* unrequested foreground props on new-scene generations remain hard QA failures;
* visible recurring-partner faces get a natural, photogenic quality target without
  changing the canonical identity anchor;
* scene-critical partner photos get an independent fail-closed scene review so a
  street/sidewalk cannot pass as a rooftop merely because city lights are visible.
"""

import asyncio
import logging
import re
from contextvars import ContextVar

from app.llm import image_client as _image_client
from app.services import image_generation_service as _base
from app.services import image_pipeline_v2 as _v2
from app.services import partner_image_quality_policy as _partner_quality


logger = logging.getLogger(__name__)

_partner_identity_locked: ContextVar[bool] = ContextVar(
    "partner_identity_locked_image_generation",
    default=False,
)
_runtime_patch_lock = asyncio.Lock()

_original_model_plan = _base.build_generation_model_plan
_original_attempt_plan = _base.build_generation_attempt_plan


class _FalseyBodyVisibility(dict):
    """Preserve body/framing fields while preventing them from implying adult intent."""

    def __bool__(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Generic parser boundary
# ---------------------------------------------------------------------------
_existing_parse = _v2.parse_image_intent
if getattr(_existing_parse, "_moones_nonadult_region_safe", False):
    _original_parse_image_intent = getattr(
        _existing_parse,
        "_moones_original_parse_image_intent",
        _existing_parse,
    )
else:
    _original_parse_image_intent = _existing_parse


_ADULT_REGION_ESCALATION = frozenset({"breasts", "buttocks", "genitals"})
_ADULT_INTERACTIONS = frozenset({"kiss", "hug", "holding_hands"})


def _runtime_parse_image_intent(request):
    """Undo only the legacy *ordinary body-region => suggestive* fallback."""
    intent = _original_parse_image_intent(request)
    classification = str(getattr(intent, "content_classification", "") or "").lower()
    if classification != str(_v2.ContentClassification.SUGGESTIVE):
        return intent
    if getattr(intent, "adult_intent", None):
        return intent
    if getattr(getattr(intent, "wardrobe", None), "wardrobe", None) == "lingerie":
        return intent
    if getattr(intent, "interaction", None) in _ADULT_INTERACTIONS:
        return intent

    regions = getattr(getattr(intent, "body_visibility", None), "regions", {}) or {}
    sensitive_visible = any(
        region in _ADULT_REGION_ESCALATION
        and bool(getattr(region_intent, "visibility_requested", False))
        and not bool(getattr(region_intent, "visibility_negated", False))
        for region, region_intent in regions.items()
    )
    if sensitive_visible:
        return intent

    intent.content_classification = _v2.ContentClassification.NORMAL
    return intent


_runtime_parse_image_intent._moones_nonadult_region_safe = True
_runtime_parse_image_intent._moones_original_parse_image_intent = (
    _original_parse_image_intent
)
_v2.parse_image_intent = _runtime_parse_image_intent


# ---------------------------------------------------------------------------
# Fresh-scene context boundary
# ---------------------------------------------------------------------------
_existing_merge = _v2.merge_image_intent
if getattr(_existing_merge, "_moones_fresh_scene_safe", False):
    _original_merge_image_intent = getattr(
        _existing_merge,
        "_moones_original_merge_image_intent",
        _existing_merge,
    )
else:
    _original_merge_image_intent = _existing_merge

_SCENE_COUPLED_FIELDS = frozenset(
    {
        "activity",
        "pose",
        "support_surface",
        "camera",
        "framing",
        "held_objects",
        "visible_objects",
    }
)


def _runtime_merge_image_intent(
    current_intent,
    source_plan=None,
    recent_context=None,
    memory_context=None,
    routine_context=None,
):
    """Prevent prior-image scene state from contaminating a fresh explicit scene.

    Identity and wardrobe continuity may survive. Scene-coupled state (old reading
    activity, chair, book, camera mode, etc.) may not survive when the current
    request explicitly names a new location/scene unless the field is itself
    explicit in the current request.
    """
    merged = _original_merge_image_intent(
        current_intent,
        source_plan,
        recent_context=recent_context,
        memory_context=memory_context,
        routine_context=routine_context,
    )
    scene = getattr(current_intent, "scene", None)
    explicit_scene = bool(
        getattr(scene, "explicit_current_request", False)
        and (getattr(scene, "scene_key", None) or getattr(scene, "location", None))
    )
    if not explicit_scene:
        return merged

    for name in _SCENE_COUPLED_FIELDS:
        field = merged.get(name)
        if not isinstance(field, _v2.ResolvedField):
            continue
        if bool(getattr(field, "explicit_current_request", False)):
            continue
        source = str(getattr(field, "source", "") or "")
        if source in {
            str(_v2.Provenance.SOURCE_PLAN),
            str(_v2.Provenance.RECENT),
            str(_v2.Provenance.ROUTINE),
        }:
            merged[name] = _v2.ResolvedField(None, _v2.Provenance.SYSTEM)
            logger.info(
                "IMAGE_FRESH_SCENE_STALE_FIELD_DROPPED field=%s old_source=%s scene=%s location=%s",
                name,
                source,
                getattr(scene, "scene_key", None),
                getattr(scene, "location", None),
            )
    return merged


_runtime_merge_image_intent._moones_fresh_scene_safe = True
_runtime_merge_image_intent._moones_original_merge_image_intent = (
    _original_merge_image_intent
)
_v2.merge_image_intent = _runtime_merge_image_intent


# ---------------------------------------------------------------------------
# Generic compiler boundary
# ---------------------------------------------------------------------------
_existing_compile = _v2.compile_image_prompt
if getattr(_existing_compile, "_moones_normal_prompt_safe", False):
    _original_compile_image_prompt = getattr(
        _existing_compile,
        "_moones_original_compile_image_prompt",
        _existing_compile,
    )
else:
    _original_compile_image_prompt = _existing_compile


def _runtime_compile_image_prompt(plan):
    """Compile safely, then enforce stable natural partner-face quality."""
    classification = str(
        (getattr(plan, "current_intent", None) or {}).get("content_classification")
        or ""
    ).lower()
    is_normal = classification == str(_v2.ContentClassification.NORMAL)

    if is_normal:
        original_body_visibility = getattr(plan, "body_visibility", None)
        try:
            plan.body_visibility = _FalseyBodyVisibility(original_body_visibility or {})
            compiled = _original_compile_image_prompt(plan)
        finally:
            plan.body_visibility = original_body_visibility

        compiled.positive_prompt = compiled.positive_prompt.replace(
            "Never change the stored gender presentation or anatomical profile. "
            "Do not replace the partner with a generic woman or generic man.",
            "Never change the stored gender presentation or canonical visual identity. "
            "Do not replace the partner with a generic woman or generic man.",
        )
    else:
        compiled = _original_compile_image_prompt(plan)

    return _partner_quality.apply_partner_face_quality(compiled, plan)


_runtime_compile_image_prompt._moones_normal_prompt_safe = True
_runtime_compile_image_prompt._moones_original_compile_image_prompt = (
    _original_compile_image_prompt
)
_v2.compile_image_prompt = _runtime_compile_image_prompt


# ---------------------------------------------------------------------------
# Provider-only age representation
# ---------------------------------------------------------------------------
_existing_adapt_provider_prompts = _image_client.adapt_provider_prompts
if getattr(_existing_adapt_provider_prompts, "_moones_provider_age_safe", False):
    _original_adapt_provider_prompts = getattr(
        _existing_adapt_provider_prompts,
        "_moones_original_adapt_provider_prompts",
        _existing_adapt_provider_prompts,
    )
else:
    _original_adapt_provider_prompts = _existing_adapt_provider_prompts


def _provider_age_appearance(age: int) -> str:
    if age <= 24:
        return "adult appearance in the early twenties"
    if age <= 29:
        return "adult appearance in the late twenties"
    if age <= 39:
        return "adult appearance in the thirties"
    if age <= 49:
        return "adult appearance in the forties"
    if age <= 59:
        return "mature adult appearance in the fifties"
    return "mature adult appearance"


def _sanitize_system_profile_age_for_provider(prompt: str) -> tuple[str, bool]:
    text = str(prompt or "")
    changed = False

    def replace_identity(match: re.Match) -> str:
        nonlocal changed
        age = int(match.group(1))
        if age < 18:
            return match.group(0)
        changed = True
        return f"age_profile={_provider_age_appearance(age)}"

    def replace_overlay(match: re.Match) -> str:
        nonlocal changed
        age = int(match.group(1))
        if age < 18:
            return match.group(0)
        changed = True
        return _provider_age_appearance(age)

    text = re.sub(
        r"\bfictional_age\s*=\s*(\d{2,3})\b",
        replace_identity,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bfictional\s+age\s+(\d{2,3})\b",
        replace_overlay,
        text,
        flags=re.IGNORECASE,
    )
    return text, changed


def _runtime_adapt_provider_prompts(
    model: str,
    prompt: str,
    negative_prompt: str,
):
    safe_prompt, age_sanitized = _sanitize_system_profile_age_for_provider(prompt)
    adapted_prompt, adapted_negative, metadata = _original_adapt_provider_prompts(
        model,
        safe_prompt,
        negative_prompt,
    )
    metadata = dict(metadata or {})
    metadata["provider_profile_age_sanitized"] = bool(age_sanitized)
    return adapted_prompt, adapted_negative, metadata


_runtime_adapt_provider_prompts._moones_provider_age_safe = True
_runtime_adapt_provider_prompts._moones_original_adapt_provider_prompts = (
    _original_adapt_provider_prompts
)
_image_client.adapt_provider_prompts = _runtime_adapt_provider_prompts


# ---------------------------------------------------------------------------
# Persistent-partner provider and QA policy
# ---------------------------------------------------------------------------
def _runtime_model_plan(
    settings,
    primary_model: str,
    *,
    adult_generation: bool,
    identity_locked_generation: bool = False,
) -> list[str]:
    """Keep every recurring-partner photo on Krea -> Seedream only."""
    if adult_generation or identity_locked_generation or _partner_identity_locked.get():
        return [
            _base.ADULT_PRIMARY_GENERATION_MODEL,
            _base.ADULT_FALLBACK_GENERATION_MODEL,
        ]
    return _original_model_plan(
        settings,
        primary_model,
        adult_generation=adult_generation,
    )


def _runtime_attempt_plan(
    model_plan: list[str],
    *,
    adult_generation: bool,
    max_attempts: int,
    identity_locked_generation: bool = False,
) -> list[tuple[str, int]]:
    """Give a recurring-partner image four bounded slots, scene-agnostically."""
    if identity_locked_generation or _partner_identity_locked.get():
        available = list(
            dict.fromkeys(
                str(model or "").strip()
                for model in model_plan
                if str(model or "").strip()
            )
        )
        attempts: list[tuple[str, int]] = []
        primary = _base.ADULT_PRIMARY_GENERATION_MODEL
        fallback = _base.ADULT_FALLBACK_GENERATION_MODEL
        if primary in available:
            attempts.extend([(primary, 0), (primary, 1)])
        if fallback in available:
            attempts.extend([(fallback, 0), (fallback, 0)])
        return attempts[:4]
    return _original_attempt_plan(
        model_plan,
        adult_generation=adult_generation,
        max_attempts=max_attempts,
        identity_locked_generation=identity_locked_generation,
    )


def _accept_foreground_prop_only_as_advisory(qa, *, visual_requirements: dict | None):
    """Legacy helper retained for same-scene refinements and focused regressions."""
    if qa is None:
        return qa
    codes = list(getattr(qa, "reason_codes", None) or [])
    if set(codes) != {"unrequested_foreground_object"}:
        return qa
    requirements = visual_requirements or {}
    if bool(
        requirements.get("explicit_nudity_requested")
        or requirements.get("anatomy_qa_required")
    ):
        return qa
    if str(getattr(qa, "confidence", "low") or "low").lower() not in {
        "medium",
        "high",
    }:
        return qa

    raw = list(getattr(qa, "raw_provider_reason_codes", None) or [])
    qa.passed = True
    qa.reason_codes = []
    setattr(
        qa,
        "raw_provider_reason_codes",
        list(dict.fromkeys(raw + codes)),
    )
    setattr(qa, "qa_advisory_foreground_object", True)
    logger.info(
        "IMAGE_PARTNER_QA_FOREGROUND_PROP_ADVISORY labels=%s confidence=%s",
        getattr(qa, "unrequested_foreground_object_labels", None),
        getattr(qa, "confidence", None),
    )
    return qa


claim_next_job = _base.claim_next_job
cleanup_stale_artifacts = _base.cleanup_stale_artifacts


async def process_job(
    db,
    job,
    *,
    image_client=None,
    telegram_service=None,
    generated_image_qa_evaluator=None,
    strict_scene_guard_evaluator=None,
):
    """Run one job with temporary, fully-restored partner routing overrides."""
    metadata = dict(getattr(job, "metadata_json", None) or {})
    locked = _base.partner_identity_generation_required(metadata)
    job_requirements = dict(metadata.get("visual_requirements") or {})
    route_action = str(metadata.get("route_action") or getattr(job, "image_action", "") or "")
    qa_delegate = generated_image_qa_evaluator or _base.evaluate_single_subject_image

    async def partner_qa_evaluator(*args, **kwargs):
        qa = await qa_delegate(*args, **kwargs)
        if not locked:
            return qa

        # A stale book/cup/prop in a new scene is a real fulfillment regression,
        # not harmless decoration. Keep the old advisory relaxation only for an
        # explicit same-scene refinement path.
        if route_action in {"refinement", "refine_previous"}:
            qa = _accept_foreground_prop_only_as_advisory(
                qa,
                visual_requirements=job_requirements,
            )
        if not getattr(qa, "passed", False):
            return qa

        # Unit/integration tests often inject a local QA evaluator. Do not let
        # those tests accidentally call the live Vision provider unless they
        # explicitly inject the strict scene reviewer too. Production passes no
        # custom QA evaluator and therefore always runs the real scene guard.
        if generated_image_qa_evaluator is not None and strict_scene_guard_evaluator is None:
            return qa

        image_bytes = args[0] if args else kwargs.get("image_bytes")
        return await _partner_quality.enforce_strict_partner_scene_guard(
            image_bytes,
            qa,
            visual_requirements=job_requirements,
            analyzer=strict_scene_guard_evaluator,
        )

    async with _runtime_patch_lock:
        token = _partner_identity_locked.set(bool(locked))
        previous_model_plan = _base.build_generation_model_plan
        previous_attempt_plan = _base.build_generation_attempt_plan
        _base.build_generation_model_plan = _runtime_model_plan
        _base.build_generation_attempt_plan = _runtime_attempt_plan
        try:
            return await _base.process_job(
                db,
                job,
                image_client=image_client,
                telegram_service=telegram_service,
                generated_image_qa_evaluator=partner_qa_evaluator,
            )
        finally:
            _base.build_generation_model_plan = previous_model_plan
            _base.build_generation_attempt_plan = previous_attempt_plan
            _partner_identity_locked.reset(token)
