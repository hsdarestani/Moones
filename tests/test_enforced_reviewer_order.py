from types import SimpleNamespace

from app.services.generated_image_qa_service import _configured_vision_reviewer_models


PRIMARY = "qwen3-vl-235b-a22b"
MISTRAL = "mistral-31-24b"
EMERGENCY = "e2ee-qwen3-vl-30b-a3b-p"


def test_stale_legacy_env_cannot_remove_mistral_from_second_position():
    settings = SimpleNamespace(
        vision_model=PRIMARY,
        vision_reviewer_models=f"{PRIMARY},{EMERGENCY}",
        vision_fallback_model=EMERGENCY,
    )

    assert _configured_vision_reviewer_models(settings, max_models=3) == [
        PRIMARY,
        MISTRAL,
        EMERGENCY,
    ]


def test_stale_fallback_only_env_still_uses_independent_secondary():
    settings = SimpleNamespace(
        vision_model=PRIMARY,
        vision_reviewer_models="",
        vision_fallback_model=EMERGENCY,
    )

    assert _configured_vision_reviewer_models(settings, max_models=2) == [
        PRIMARY,
        MISTRAL,
    ]


def test_legacy_reviewer_cannot_be_promoted_to_primary_role():
    settings = SimpleNamespace(
        vision_model=EMERGENCY,
        vision_reviewer_models=f"{EMERGENCY},{PRIMARY}",
        vision_fallback_model=EMERGENCY,
    )

    assert _configured_vision_reviewer_models(settings, max_models=3) == [
        PRIMARY,
        MISTRAL,
        EMERGENCY,
    ]


def test_additional_configured_reviewers_are_appended_after_required_roles():
    settings = SimpleNamespace(
        vision_model="custom-primary",
        vision_reviewer_models="custom-primary,custom-fourth",
        vision_fallback_model="custom-fifth",
    )

    assert _configured_vision_reviewer_models(settings) == [
        "custom-primary",
        MISTRAL,
        EMERGENCY,
        "custom-fourth",
        "custom-fifth",
    ]
