from __future__ import annotations

"""Runtime policy adapter for recurring-partner image generation.

The core worker stays reusable for legacy/object/pet routes. The production app
imports this adapter so any image that visibly represents the persistent partner
gets one scene-agnostic delivery contract:

    Krea base -> same-seed Krea QA correction -> Seedream fallback
    -> one final Seedream retry before refund.

The adapter also closes a compiler boundary bug: NORMAL image requests must not
enter the adult prompt branch merely because body/framing visibility metadata is
non-empty. This decision is classification-based, never scene/activity-based.
"""

import asyncio
from contextvars import ContextVar

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
    """Compile NORMAL prompts without leaking adult/anatomy policy language.

    image_pipeline_v2 historically treated ``bool(plan.body_visibility)`` as a
    second adult-intent signal. That is incorrect for ordinary requests such as
    full-body portraits, visible hands, wardrobe views, or any other framing
    requirement. A falsey mapping keeps every body-visibility entry available to
    the compiler while making adult branching depend only on the already-resolved
    content classification.
    """
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
# This is a deliberate process-wide compiler policy: all V2 enqueue/validation
# callers must share the same normal-vs-adult boundary. Unlike the worker planner
# overrides below, it is pure/deterministic and carries no per-job mutable state.
_v2.compile_image_prompt = _runtime_compile_image_prompt


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
    """Give a recurring-partner image four bounded slots, scene-agnostically.

    The final Seedream slot intentionally uses correction_round=0. In the core
    worker that means it is not skipped after a Seedream provider/transport
    failure. If the prior Seedream result was an actual image rejected by QA,
    the worker still adds the corrective prompt because Seedream is the fallback
    model. The later attempt index supplies a fresh deterministic Seedream seed.
    """
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
            # Two base-labelled Seedream slots are deliberate: the second one
            # survives either a provider failure or a QA rejection of the first.
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

    # process_job resolves these planner names from image_generation_service
    # globals while it runs. Restrict the override to one serialized invocation
    # so importing this module never mutates the core worker and no concurrent
    # job can observe another job's temporary provider policy.
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
