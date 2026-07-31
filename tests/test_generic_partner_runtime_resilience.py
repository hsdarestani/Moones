from types import SimpleNamespace

from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base
from app.services import image_pipeline_v2 as v2


def test_importing_runtime_does_not_patch_core_worker_globally():
    assert base.build_generation_model_plan is not runtime._runtime_model_plan
    assert base.build_generation_attempt_plan is not runtime._runtime_attempt_plan
    assert base.build_generation_model_plan is runtime._original_model_plan
    assert base.build_generation_attempt_plan is runtime._original_attempt_plan


def test_runtime_installs_one_shared_safe_v2_compiler_policy():
    assert v2.compile_image_prompt is runtime._runtime_compile_image_prompt
    assert runtime._runtime_compile_image_prompt._moones_normal_prompt_safe is True


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
        + inspect.getsource(runtime._runtime_compile_image_prompt)
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


def _minimal_plan(*, classification, body_visibility=None, anatomy=False):
    vr = v2.VisualRequirements(
        anatomical_profile="female" if anatomy else None,
        anatomy_consistency_required=anatomy,
        explicit_nudity_requested=anatomy,
        photo_contract={
            "primary_subject": "partner",
            "partner_visible": True,
            "identity_consistency_required": True,
        },
    )
    return v2.ResolvedImagePlan(
        current_intent={"content_classification": classification},
        body_visibility=body_visibility or {},
        identity={
            "descriptor": {
                "partner_name": "Mahnaz",
                "fictional_age": 18,
                "gender_presentation": "feminine",
                "face": "long oval face, soft tapered jaw",
                "hair": "deep brown soft wavy hair",
                "eyes": "dark brown almond eyes",
                "skin": "light olive skin",
                "body": "average healthy adult build",
            },
            "identity_fingerprint": "stable-test-fingerprint",
            "continuity": {},
        },
        composition={"expected_subject_count": 1},
        visual_requirements=vr,
    )


def test_normal_body_visibility_does_not_leak_adult_prompt_contract():
    plan = _minimal_plan(
        classification=v2.ContentClassification.NORMAL,
        body_visibility={
            "full_body": {
                "visibility_requested": True,
                "framing_requested": True,
            }
        },
    )
    compiled = v2.compile_image_prompt(plan)
    positive = compiled.positive_prompt.lower()
    negative = compiled.negative_prompt.lower()

    assert "canonical identity lock" in positive
    assert "canonical visual identity" in positive
    assert "anatomical profile" not in positive
    assert "adult anatomy consistency:" not in positive
    assert "body visibility: full_body" not in positive
    for term in (
        "contradictory anatomy",
        "mixed sex characteristics inconsistent with profile",
        "malformed anatomy",
        "ambiguous anatomy",
        "anatomically inconsistent body",
    ):
        assert term not in negative


def test_explicit_adult_classification_still_uses_adult_prompt_contract():
    plan = _minimal_plan(
        classification=v2.ContentClassification.FULL_NUDITY,
        body_visibility={
            "full_body": {
                "visibility_requested": True,
                "framing_requested": True,
            }
        },
        anatomy=True,
    )
    compiled = v2.compile_image_prompt(plan)
    positive = compiled.positive_prompt.lower()
    negative = compiled.negative_prompt.lower()

    assert "adult anatomy consistency:" in positive
    assert "anatomical profile" in positive
    assert "contradictory anatomy" in negative
