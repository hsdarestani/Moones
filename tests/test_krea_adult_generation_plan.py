from types import SimpleNamespace

from app.core.config import Settings
from app.services.generated_image_qa_service import corrective_prompt_for_reasons
from app.services.image_generation_guardrails import select_generation_model
from app.services.image_generation_service import (
    build_generation_attempt_plan,
    build_generation_model_plan,
)
from app.services.image_pipeline_v2 import ContentClassification


def test_krea_is_default_for_normal_and_adult_generation():
    settings = Settings()
    assert settings.image_generation_preferred_model == "krea-2-turbo"
    assert settings.image_generation_model == "krea-2-turbo"
    assert settings.image_generation_adult_preferred_model == "krea-2-turbo"
    assert settings.image_generation_adult_model == "krea-2-turbo"


def test_adult_model_selection_uses_krea_not_lustify():
    selected = select_generation_model(
        content_classification=ContentClassification.FULL_NUDITY,
        default_model="krea-2-turbo",
        adult_model="krea-2-turbo",
    )
    assert selected == "krea-2-turbo"


def test_adult_model_plan_keeps_krea_then_both_lustify_fallbacks():
    settings = SimpleNamespace(
        image_generation_adult_preferred_model="krea-2-turbo",
        image_generation_adult_model="krea-2-turbo",
        image_generation_adult_fallback_model="lustify-sdxl",
        image_generation_adult_emergency_models="lustify-v8",
    )
    assert build_generation_model_plan(
        settings, "krea-2-turbo", adult_generation=True
    ) == ["krea-2-turbo", "lustify-sdxl", "lustify-v8"]


def test_adult_attempt_plan_retries_krea_before_fallback():
    assert build_generation_attempt_plan(
        ["krea-2-turbo", "lustify-sdxl", "lustify-v8"],
        adult_generation=True,
        max_attempts=4,
    ) == [
        ("krea-2-turbo", 0),
        ("krea-2-turbo", 1),
        ("lustify-sdxl", 0),
        ("lustify-v8", 0),
    ]


def test_full_body_correction_is_composition_specific():
    correction = corrective_prompt_for_reasons(
        ["framing_mismatch", "missing_feet", "cropped_body"],
        photo_contract={"camera_mode": "mirror_selfie"},
    ).lower()
    assert "head-to-feet" in correction
    assert "headroom" in correction
    assert "floor below both feet" in correction
    assert "70 percent" in correction
    assert "mirror" in correction
