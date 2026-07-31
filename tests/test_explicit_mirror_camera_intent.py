from types import SimpleNamespace

from app.services import image_pipeline_v2 as v2


def _profile():
    return SimpleNamespace(
        profile_json={
            "identity_anchor": {
                "face": "oval adult face",
                "hair": "dark shoulder-length hair",
                "eyes": "dark almond-shaped eyes",
                "skin": "olive skin",
                "body": "natural adult feminine build",
                "distinguishing_details": "natural eyebrows",
            },
            "identity_anchor_fingerprint": "mirror-camera-test-fingerprint",
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
        face_description="oval adult face",
        hair_description="dark shoulder-length hair",
        eye_description="dark almond-shaped eyes",
        skin_description="olive skin",
        body_description="natural adult feminine build",
        height_impression="average height",
        distinguishing_details="natural eyebrows",
        updated_at=None,
    )


def test_in_front_of_mirror_explicitly_resolves_mirror_selfie_camera():
    request = "یه عکس تمام‌قد کاملاً لخت جلوی آینه توی اتاقت بده"
    intent = v2.parse_image_intent(v2.normalize_request_v2(request))

    assert intent.scene.scene_key == "mirror"
    assert any(
        relation.relation == "in_front_of" and relation.object == "mirror"
        for relation in intent.scene.spatial_relations
    )
    assert intent.composition.camera == "mirror_selfie"
    assert intent.composition.framing == "full_body"

    merged = v2.merge_image_intent(
        intent,
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    plan = v2.construct_resolved_plan(
        intent,
        merged,
        v2.SafetyDecision(v2.PolicyDecision.ALLOW),
        _profile(),
        message_id=1,
        user_request=request,
    )

    assert plan.camera.value == "mirror_selfie"
    assert plan.visual_requirements.camera_mode == "mirror_selfie"
    assert plan.visual_requirements.framing_requirement == "full_body"


def test_rooftop_full_body_does_not_turn_into_mirror_or_selfie():
    request = "حالا یه عکس تمام‌قد از خودت روی پشت‌بوم یه ساختمون شب، باد موهاتو به‌هم زده، لباس مشکی رسمی پوشیدی و چراغ‌های شهر پشت سرت معلومه."
    intent = v2.parse_image_intent(v2.normalize_request_v2(request))

    assert intent.composition.camera is None
    assert intent.composition.framing == "full_body"

    vr = v2.resolve_visual_requirements(intent, user_request=request)
    assert vr.camera_mode == "tripod_timer"
    assert vr.framing_requirement == "full_body"


def test_explicit_selfie_is_camera_not_framing():
    intent = v2.parse_image_intent(v2.normalize_request_v2("یه سلفی از خودت بده"))
    assert intent.composition.camera == "selfie"
    assert intent.composition.framing is None
