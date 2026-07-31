from __future__ import annotations

"""Cost-aware image model routing policy.

Product contract:
- every adult image request uses Krea 2 Turbo only;
- every non-adult image request uses Seedream V5 Lite only;
- no cross-tier fallback between Krea and Seedream;
- retries, when needed, stay on the same routed model and are bounded to one
  correction attempt.

This module patches the existing image-generation service/runtime boundary in one
place so stale environment model settings cannot override the product contract.
It performs no network calls.
"""

from app.services import image_generation_guardrails as _guardrails
from app.services import image_generation_runtime as _runtime
from app.services import image_generation_service as _base

ADULT_IMAGE_MODEL = "krea-2-turbo"
STANDARD_IMAGE_MODEL = "seedream-v5-lite"

_ADULT_CLASSIFICATIONS = frozenset(
    {
        "suggestive",
        "lingerie",
        "topless",
        "full_nudity",
    }
)


def _normalized_enum_value(value: object) -> str:
    text = str(value or "").strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def content_classification_is_adult(value: object) -> bool:
    return _normalized_enum_value(value) in _ADULT_CLASSIFICATIONS


def select_generation_model(
    *,
    content_classification: object,
    default_model: str,
    adult_model: str | None,
) -> str:
    """Route solely by resolved content class, never by stale env preferences."""
    del default_model, adult_model
    return ADULT_IMAGE_MODEL if content_classification_is_adult(content_classification) else STANDARD_IMAGE_MODEL


def build_generation_model_plan(
    settings,
    primary_model: str,
    *,
    adult_generation: bool,
) -> list[str]:
    """Return exactly one model; never cross-fallback between cost tiers."""
    del settings
    adult = bool(adult_generation or str(primary_model or "").strip() == ADULT_IMAGE_MODEL)
    return [ADULT_IMAGE_MODEL if adult else STANDARD_IMAGE_MODEL]


def build_generation_attempt_plan(
    model_plan: list[str],
    *,
    adult_generation: bool,
    max_attempts: int,
    identity_locked_generation: bool = False,
) -> list[tuple[str, int]]:
    """Stay on the routed model and cap generation to base + one correction."""
    if not model_plan:
        return []

    first = str(model_plan[0] or "").strip()
    adult = bool(adult_generation or first == ADULT_IMAGE_MODEL)
    model = ADULT_IMAGE_MODEL if adult else STANDARD_IMAGE_MODEL
    limit = max(1, min(2, int(max_attempts or 1)))

    attempts: list[tuple[str, int]] = [(model, 0)]
    # Keep one bounded correction for recurring-partner identity/QA or adult QA.
    if limit > 1 and (adult or identity_locked_generation):
        attempts.append((model, 1))
    return attempts[:limit]


def _runtime_model_plan(
    settings,
    primary_model: str,
    *,
    adult_generation: bool,
    identity_locked_generation: bool = False,
) -> list[str]:
    # Identity continuity must not silently promote an ordinary image to Krea.
    del identity_locked_generation
    return build_generation_model_plan(
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
    return build_generation_attempt_plan(
        model_plan,
        adult_generation=adult_generation,
        max_attempts=max_attempts,
        identity_locked_generation=identity_locked_generation,
    )


def install_image_model_routing_policy() -> None:
    """Install the strict routing policy into enqueue and worker paths."""
    # Keep the legacy constants coherent for diagnostics/metadata, but remove
    # Seedream as an adult fallback entirely.
    _base.ADULT_PRIMARY_GENERATION_MODEL = ADULT_IMAGE_MODEL
    _base.ADULT_FALLBACK_GENERATION_MODEL = ""
    _base.ADULT_ALLOWED_GENERATION_MODELS = (ADULT_IMAGE_MODEL,)

    _guardrails.select_generation_model = select_generation_model
    _base.select_generation_model = select_generation_model
    _base.build_generation_model_plan = build_generation_model_plan
    _base.build_generation_attempt_plan = build_generation_attempt_plan

    # image_generation_runtime temporarily swaps these functions into the base
    # worker while processing a recurring-partner job, so patch its adapters too.
    _runtime._runtime_model_plan = _runtime_model_plan
    _runtime._runtime_attempt_plan = _runtime_attempt_plan
