from types import SimpleNamespace

from app.core.config import Settings
from app.services.generated_image_qa_service import corrective_prompt_for_reasons
from app.services.image_generation_guardrails import select_generation_model
from app.services.image_generation_service import (
    ADULT_ALLOWED_GENERATION_MODELS,
    build_generation_attempt_plan,
    build_generation_model_plan,
)
from app.services.image_pipeline_v2 import ContentClassification


def test_krea_is_default_and_seedream_is_only_adult_fallback():
    settings = Settings()
    assert settings.image_generation_adult_preferred_model == "krea-2-turbo"
    assert settings.image_generation_adult_model == "krea-2-turbo"
    assert settings.image_generation_adult_fallback_model == "seedream-v5-lite"
    assert settings.image_generation_adult_emergency_models == ""
    assert settings.image_generation_adult_max_generation_attempts == 4


def test_adult_model_selection_uses_krea():
    selected = select_generation_model(
        content_classification=ContentClassification.FULL_NUDITY,
        default_model="krea-2-turbo",
        adult_model="krea-2-turbo",
    )
    assert selected == "krea-2-turbo"


def test_adult_model_plan_is_strict_despite_stale_legacy_env():
    settings = SimpleNamespace(
        image_generation_adult_preferred_model="lustify-sdxl",
        image_generation_adult_model="venice-sd35",
        image_generation_adult_fallback_model="lustify-v8",
        image_generation_adult_emergency_models="z-image-turbo,lustify-v7",
    )
    plan = build_generation_model_plan(
        settings, "lustify-sdxl", adult_generation=True
    )
    assert plan == ["krea-2-turbo", "seedream-v5-lite"]
    assert tuple(plan) == ADULT_ALLOWED_GENERATION_MODELS
    assert not ({"lustify-sdxl", "lustify-v8", "venice-sd35", "z-image-turbo"} & set(plan))


def test_adult_attempt_plan_is_krea_retry_then_seedream_retry_only():
    assert build_generation_attempt_plan(
        ["krea-2-turbo", "seedream-v5-lite"],
        adult_generation=True,
        max_attempts=4,
    ) == [
        ("krea-2-turbo", 0),
        ("krea-2-turbo", 1),
        ("seedream-v5-lite", 0),
        ("seedream-v5-lite", 1),
    ]


def test_full_body_correction_preserves_identity_and_changes_composition_only():
    correction = corrective_prompt_for_reasons(
        ["framing_mismatch", "missing_feet", "cropped_body"],
        identity_requirements={"face": "stable fictional face"},
        photo_contract={"camera_mode": "mirror_selfie"},
    ).lower()
    assert "exact stored fictional identity" in correction
    assert "facial geometry" in correction
    assert "may change only framing" in correction
    assert "head-to-feet" in correction
    assert "floor below both feet" in correction
    assert "70 percent" in correction
