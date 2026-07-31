from types import SimpleNamespace

from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base


def test_runtime_partner_model_plan_is_strict_krea_then_seedream():
    settings = SimpleNamespace(
        image_generation_preferred_model="legacy-preferred",
        image_generation_model="legacy-model",
        image_generation_fallback_model="legacy-fallback",
        image_generation_emergency_models="legacy-emergency",
    )
    token = runtime._partner_identity_locked.set(True)
    try:
        assert runtime._runtime_model_plan(
            settings,
            "legacy-primary",
            adult_generation=False,
        ) == [
            base.ADULT_PRIMARY_GENERATION_MODEL,
            base.ADULT_FALLBACK_GENERATION_MODEL,
        ]
    finally:
        runtime._partner_identity_locked.reset(token)


def test_runtime_partner_attempt_plan_has_krea_retry_and_two_seedream_slots():
    token = runtime._partner_identity_locked.set(True)
    try:
        assert runtime._runtime_attempt_plan(
            [base.ADULT_PRIMARY_GENERATION_MODEL, base.ADULT_FALLBACK_GENERATION_MODEL],
            adult_generation=False,
            identity_locked_generation=True,
            max_attempts=3,
        ) == [
            (base.ADULT_PRIMARY_GENERATION_MODEL, 0),
            (base.ADULT_PRIMARY_GENERATION_MODEL, 1),
            (base.ADULT_FALLBACK_GENERATION_MODEL, 0),
            (base.ADULT_FALLBACK_GENERATION_MODEL, 0),
        ]
    finally:
        runtime._partner_identity_locked.reset(token)


def test_runtime_policy_is_scene_agnostic():
    import inspect

    source = (
        inspect.getsource(runtime._runtime_model_plan)
        + inspect.getsource(runtime._runtime_attempt_plan)
        + inspect.getsource(runtime.process_job)
    ).lower()
    for scenario in (
        "cafe",
        "bookstore",
        "rooftop",
        "bed",
        "bathroom",
        "mirror",
        "park",
        "street",
    ):
        assert scenario not in source


def test_object_only_metadata_does_not_activate_partner_identity_lock():
    metadata = {
        "expected_subject_count": 0,
        "identity_seed": 123,
        "visual_requirements": {
            "partner_visible": False,
            "photo_contract": {
                "primary_subject": "object",
                "object_only": True,
                "partner_visible": False,
            },
        },
    }
    assert base.partner_identity_generation_required(metadata) is False
