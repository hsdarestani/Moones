from __future__ import annotations

"""Runtime policy adapter for recurring-partner image generation.

The core worker stays reusable for legacy/object/pet routes. The production app
imports this adapter so any image that visibly represents the persistent partner
gets one scene-agnostic delivery contract:

    Krea base -> same-seed Krea QA correction -> Seedream fallback
    -> one final Seedream retry before refund.

The adapter also closes two generic prompt-routing boundaries:

* ordinary visible regions (hair, face, eyes, hands, arms, etc.) must never turn
  an otherwise NORMAL request into an adult/suggestive request;
* numeric edge ages from the persistent profile stay authoritative throughout the
  internal plan/compiled prompt, while only provider-bound text uses an equivalent
  unambiguously-adult visual age band.

These decisions are classification/profile based, never scene/activity based.
"""

import asyncio
import re
from contextvars import ContextVar

from app.llm import image_client as _image_client
from app.services import image_generation_service as _base
from app.services import image_pipeline_v2 as _v2


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
    """Undo only the legacy *ordinary body-region => suggestive* fallback.

    The core parser intentionally recognizes many visible human features as body
    regions so composition can mention hair, eyes, face, hands, arms, etc. A
    legacy catch-all later promoted every visible region except ``full_body`` to
    SUGGESTIVE. That made harmless arbitrary scenes adult merely because the user
    mentioned hair or a hand.

    We downgrade only when all independent adult signals are absent. Explicit
    adult intent, lingerie, adult-sensitive regions, and romantic interactions
    keep their original classification.
    """
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
    """Compile NORMAL prompts without leaking adult/anatomy policy language."""
    classification = str(
        (getattr(plan, "current_intent", None) or {}).get("content_classification")
        or ""
    ).lower()
    is_normal = classification == str(_v2.ContentClassification.NORMAL)
    if not is_normal:
        return _original_compile_image_prompt(plan)

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
    return compiled


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
    # Provider-facing age text must stay visually useful without boundary-age or
    # youth-coded wording. The exact configured age remains authoritative in the
    # internal plan and QA metadata; only this rendering crosses the API boundary.
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
    """Remove only system-rendered numeric fictional-age tokens from provider text.

    User-authored arbitrary numeric content is untouched. The internal resolved
    plan and compiled prompt keep the exact fictional age for continuity and QA.
    """
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


claim_next_job = _base.claim_next_job
cleanup_stale_artifacts = _base.cleanup_stale_artifacts


async def process_job(
    db,
    job,
    *,
    image_client=None,
    telegram_service=None,
    generated_image_qa_evaluator=None,
):
    """Run one job with a temporary, fully-restored partner routing override."""
    locked = _base.partner_identity_generation_required(
        getattr(job, "metadata_json", None)
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
                generated_image_qa_evaluator=generated_image_qa_evaluator,
            )
        finally:
            _base.build_generation_model_plan = previous_model_plan
            _base.build_generation_attempt_plan = previous_attempt_plan
            _partner_identity_locked.reset(token)
