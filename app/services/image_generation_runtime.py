from __future__ import annotations

"""Runtime policy adapter for recurring-partner image generation.

The core worker stays reusable for legacy/object/pet routes.  The production app
imports this adapter so any image that visibly represents the persistent partner
gets one scene-agnostic delivery contract:

    Krea base -> same-seed Krea QA correction -> Seedream fallback
    -> one final Seedream retry before refund.

No location, activity, wardrobe, pose, or user-scene names participate in the
routing decision.  The decision is based only on the semantic partner-identity
contract already stored in job metadata.
"""

from contextvars import ContextVar

from app.services import image_generation_service as _base


_partner_identity_locked: ContextVar[bool] = ContextVar(
    "partner_identity_locked_image_generation",
    default=False,
)

_original_model_plan = _base.build_generation_model_plan
_original_attempt_plan = _base.build_generation_attempt_plan


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
    """Give a partner image four bounded slots without scenario hardcoding.

    The final Seedream slot intentionally uses correction_round=0.  In the core
    worker that means it is not skipped after a Seedream provider/transport
    failure.  If the prior Seedream result was an actual image rejected by QA,
    the worker still adds the corrective prompt because Seedream is the fallback
    model.  The attempt index supplies a fresh deterministic Seedream seed.
    """
    if identity_locked_generation or _partner_identity_locked.get():
        available = list(dict.fromkeys(str(m or "").strip() for m in model_plan if str(m or "").strip()))
        attempts: list[tuple[str, int]] = []
        primary = _base.ADULT_PRIMARY_GENERATION_MODEL
        fallback = _base.ADULT_FALLBACK_GENERATION_MODEL
        if primary in available:
            attempts.extend([(primary, 0), (primary, 1)])
        if fallback in available:
            # Two base-labelled Seedream slots are deliberate: the second one
            # survives either a provider failure or a QA failure of the first.
            attempts.extend([(fallback, 0), (fallback, 0)])
        return attempts[:4]
    return _original_attempt_plan(
        model_plan,
        adult_generation=adult_generation,
        max_attempts=max_attempts,
        identity_locked_generation=identity_locked_generation,
    )


# process_job resolves these names from image_generation_service globals at
# runtime, so patching the two pure planners is sufficient; the rest of the
# worker, billing, QA, refunds and delivery remain untouched.
_base.build_generation_model_plan = _runtime_model_plan
_base.build_generation_attempt_plan = _runtime_attempt_plan

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
    locked = _base.partner_identity_generation_required(getattr(job, "metadata_json", None))
    token = _partner_identity_locked.set(bool(locked))
    try:
        return await _base.process_job(
            db,
            job,
            image_client=image_client,
            telegram_service=telegram_service,
            generated_image_qa_evaluator=generated_image_qa_evaluator,
        )
    finally:
        _partner_identity_locked.reset(token)
