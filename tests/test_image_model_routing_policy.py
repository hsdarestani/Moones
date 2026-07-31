from types import SimpleNamespace

from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base
from app.services import image_model_routing_policy as policy


policy.install_image_model_routing_policy()


def test_every_adult_content_class_routes_to_krea():
    for classification in ("suggestive", "lingerie", "topless", "full_nudity"):
        assert policy.select_generation_model(
            content_classification=classification,
            default_model="some-expensive-default",
            adult_model="some-other-adult-model",
        ) == policy.ADULT_IMAGE_MODEL


def test_nonadult_content_routes_to_seedream():
    for classification in ("normal", "", None):
        assert policy.select_generation_model(
            content_classification=classification,
            default_model="krea-2-turbo",
            adult_model="krea-2-turbo",
        ) == policy.STANDARD_IMAGE_MODEL


def test_model_plan_has_no_cross_tier_fallback():
    settings = SimpleNamespace(
        image_generation_preferred_model="krea-2-turbo",
        image_generation_model="some-other-model",
        image_generation_fallback_model="another-model",
        image_generation_emergency_models="x,y,z",
    )

    assert policy.build_generation_model_plan(
        settings,
        policy.ADULT_IMAGE_MODEL,
        adult_generation=True,
    ) == [policy.ADULT_IMAGE_MODEL]

    assert policy.build_generation_model_plan(
        settings,
        "whatever-default",
        adult_generation=False,
    ) == [policy.STANDARD_IMAGE_MODEL]


def test_identity_lock_does_not_promote_normal_photo_to_krea():
    settings = SimpleNamespace()
    assert policy._runtime_model_plan(
        settings,
        policy.STANDARD_IMAGE_MODEL,
        adult_generation=False,
        identity_locked_generation=True,
    ) == [policy.STANDARD_IMAGE_MODEL]


def test_adult_attempts_stay_on_krea_only():
    attempts = policy.build_generation_attempt_plan(
        [policy.ADULT_IMAGE_MODEL],
        adult_generation=True,
        identity_locked_generation=True,
        max_attempts=4,
    )
    assert attempts == [
        (policy.ADULT_IMAGE_MODEL, 0),
        (policy.ADULT_IMAGE_MODEL, 1),
    ]
    assert {model for model, _ in attempts} == {policy.ADULT_IMAGE_MODEL}


def test_standard_partner_attempts_stay_on_seedream_only():
    attempts = policy.build_generation_attempt_plan(
        [policy.STANDARD_IMAGE_MODEL],
        adult_generation=False,
        identity_locked_generation=True,
        max_attempts=4,
    )
    assert attempts == [
        (policy.STANDARD_IMAGE_MODEL, 0),
        (policy.STANDARD_IMAGE_MODEL, 1),
    ]
    assert {model for model, _ in attempts} == {policy.STANDARD_IMAGE_MODEL}


def test_installed_worker_adapters_match_product_contract():
    assert base.ADULT_PRIMARY_GENERATION_MODEL == policy.ADULT_IMAGE_MODEL
    assert base.ADULT_FALLBACK_GENERATION_MODEL == ""
    assert base.ADULT_ALLOWED_GENERATION_MODELS == (policy.ADULT_IMAGE_MODEL,)
    assert runtime._runtime_model_plan(
        SimpleNamespace(),
        policy.ADULT_IMAGE_MODEL,
        adult_generation=True,
        identity_locked_generation=True,
    ) == [policy.ADULT_IMAGE_MODEL]
    assert runtime._runtime_model_plan(
        SimpleNamespace(),
        policy.STANDARD_IMAGE_MODEL,
        adult_generation=False,
        identity_locked_generation=True,
    ) == [policy.STANDARD_IMAGE_MODEL]
