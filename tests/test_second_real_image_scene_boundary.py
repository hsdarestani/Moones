from types import SimpleNamespace

from app.services import image_generation_runtime as runtime  # applies the existing runtime adapters first
from app.services import image_scene_boundary_runtime as scene_boundary  # noqa: F401
from app.services import image_generation_service as generation
from app.services import image_pipeline_v2 as v2
from app.services.semantic_image_intent_router import VisualIntent


ROOFTOP_REQUEST = (
    "حالا یه عکس تمام‌قد از خودت روی پشت‌بوم یه ساختمون شب، باد موهاتو به‌هم زده، "
    "لباس مشکی رسمی پوشیدی و چراغ‌های شهر پشت سرت معلومه."
)
CAFE_REQUEST = "حالا با همین چهره یه عکس معمولی از خودت توی یه کافه دنج بده."


def _profile():
    return SimpleNamespace(
        profile_json={
            "identity_anchor": {
                "face": "stable adult face",
                "hair": "long dark hair",
                "eyes": "dark expressive eyes",
                "skin": "natural olive skin",
                "body": "natural adult build",
                "distinguishing_details": "stable brows and nose geometry",
            },
            "identity_anchor_fingerprint": "second-real-test-fingerprint",
            "anatomical_profile": "female",
            "anatomical_profile_source": "explicit_profile",
            "mutable_profile_overlays": {"fictional_age": 30},
        },
        anatomical_profile="female",
        gender_presentation="feminine",
        base_seed=123456,
        user_id=1,
        version=4,
        partner_name="مونس",
        fictional_age=30,
        face_description="stable adult face",
        hair_description="long dark hair",
        eye_description="dark expressive eyes",
        skin_description="natural olive skin",
        body_description="natural adult build",
        height_impression="average height",
        distinguishing_details="stable brows and nose geometry",
        updated_at=None,
    )


def _stale_source_plan():
    return v2.ResolvedImagePlan(
        scene=v2.ResolvedField("cafe", v2.Provenance.SOURCE_PLAN),
        location=v2.ResolvedField("cafe", v2.Provenance.SOURCE_PLAN),
        environment_type=v2.ResolvedField("cafe", v2.Provenance.SOURCE_PLAN),
        activity=v2.ResolvedField("reading", v2.Provenance.SOURCE_PLAN),
        pose=v2.ResolvedField("seated", v2.Provenance.SOURCE_PLAN),
        support_surface=v2.ResolvedField("chair", v2.Provenance.SOURCE_PLAN),
        wardrobe=v2.ResolvedField("black formal outfit", v2.Provenance.SOURCE_PLAN),
        camera=v2.ResolvedField("casual_phone_photo", v2.Provenance.SOURCE_PLAN),
        lighting=v2.ResolvedField("warm indoor", v2.Provenance.SOURCE_PLAN),
    )


def test_parser_recognized_current_cafe_is_marked_explicit_without_manual_test_mutation():
    intent = v2.parse_image_intent(v2.normalize_request_v2(CAFE_REQUEST))

    assert intent.scene.scene_key == "cafe"
    assert intent.scene.explicit_current_request is True

    merged = v2.merge_image_intent(
        intent,
        _stale_source_plan(),
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    assert merged["scene"].value == "cafe"
    assert merged["activity"].value is None
    assert merged["pose"].value is None
    assert merged["support_surface"].value is None


def test_exact_rooftop_request_accepts_freeform_explicit_scene_and_drops_stale_cafe_state():
    intent = v2.parse_image_intent(v2.normalize_request_v2(ROOFTOP_REQUEST))
    visual = VisualIntent(
        scene=None,
        location=None,
        environment_type="elevated outdoor building rooftop at night",
        privacy="public",
        required_visible_environment_elements=[
            "visible rooftop surface",
            "roof parapet or roof edge",
            "city lights behind the subject",
        ],
        scene_explicit_current_request=True,
        wardrobe="black formal outfit",
        framing="full_body",
        framing_explicit_current_request=True,
        primary_subject="partner",
        partner_visible=True,
        natural_capture_required=True,
        freeform_visual_constraints=[
            "wind tousling the subject's hair",
            "nighttime city lights behind the subject",
        ],
    )
    decision = SimpleNamespace(action="generate_new", visual_intent=visual)
    intent = generation.apply_semantic_visual_intent_to_v2_intent(intent, decision)

    assert intent.scene.explicit_current_request is True
    assert intent.photo_contract.get("explicit_scene_boundary") is True

    merged = v2.merge_image_intent(
        intent,
        _stale_source_plan(),
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    assert merged["scene"].value is None
    assert merged["location"].value is None
    assert merged["activity"].value is None
    assert merged["pose"].value is None
    assert merged["support_surface"].value is None

    source_job = SimpleNamespace(
        id=161,
        seed=111111,
        final_provider_seed=111111,
        user_id=1,
        chat_id=1,
    )
    plan = v2.construct_resolved_plan(
        intent,
        merged,
        v2.SafetyDecision(v2.PolicyDecision.ALLOW),
        _profile(),
        source_job=source_job,
        message_id=5001,
        user_request=ROOFTOP_REQUEST,
    )
    errors = v2.validate_plan_invariants(plan, source_job=source_job, user_id=1, chat_id=1)
    compiled = v2.compile_image_prompt(plan)
    prompt_errors = v2.validate_compiled_prompt(plan, compiled)

    assert errors == []
    assert prompt_errors == []
    assert plan.visual_requirements.environment_visibility_required is True
    required_scene = plan.visual_requirements.must_satisfy["required_scene_elements"]
    assert "elevated outdoor building rooftop at night" in required_scene
    assert "visible rooftop surface" in required_scene
    assert "roof parapet or roof edge" in required_scene
    assert plan.visual_requirements.framing_requirement == "full_body"


def test_generic_followup_without_new_scene_keeps_existing_scene_continuity():
    intent = v2.parse_image_intent(v2.normalize_request_v2("یه عکس قدی بده"))
    assert intent.scene.explicit_current_request is False

    merged = v2.merge_image_intent(
        intent,
        _stale_source_plan(),
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    assert merged["scene"].value == "cafe"
    assert merged["activity"].value == "reading"
    assert merged["support_surface"].value == "chair"
