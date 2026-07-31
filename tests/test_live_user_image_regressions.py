import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from app.services import image_generation_runtime as runtime
from app.services import image_generation_service as base
from app.services import image_pipeline_v2 as v2
from app.services import partner_image_quality_policy as quality


def _profile():
    return SimpleNamespace(
        profile_json={
            "identity_anchor": {
                "face": "soft oval adult face with stable jaw and chin",
                "hair": "long dark naturally textured hair",
                "eyes": "dark almond-shaped expressive eyes",
                "skin": "natural olive skin",
                "body": "natural adult feminine build",
                "distinguishing_details": "natural brows and stable nose geometry",
            },
            "identity_anchor_fingerprint": "user-regression-fingerprint",
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
        face_description="soft oval adult face with stable jaw and chin",
        hair_description="long dark naturally textured hair",
        eye_description="dark almond-shaped expressive eyes",
        skin_description="natural olive skin",
        body_description="natural adult feminine build",
        height_impression="average height",
        distinguishing_details="natural brows and stable nose geometry",
        updated_at=None,
    )


def _source_plan():
    return v2.ResolvedImagePlan(
        scene=v2.ResolvedField("cafe", v2.Provenance.SOURCE_PLAN),
        location=v2.ResolvedField("cafe", v2.Provenance.SOURCE_PLAN),
        activity=v2.ResolvedField("reading", v2.Provenance.SOURCE_PLAN),
        pose=v2.ResolvedField("seated", v2.Provenance.SOURCE_PLAN),
        support_surface=v2.ResolvedField("chair", v2.Provenance.SOURCE_PLAN),
        wardrobe=v2.ResolvedField("black formal outfit", v2.Provenance.SOURCE_PLAN),
        camera=v2.ResolvedField("tripod_timer", v2.Provenance.SOURCE_PLAN),
        lighting=v2.ResolvedField("warm indoor", v2.Provenance.SOURCE_PLAN),
    )


def _qa(*codes):
    return SimpleNamespace(
        passed=not bool(codes),
        reason_codes=list(codes),
        confidence="high",
        requested_scene_visible=True,
        unrequested_foreground_object_labels=["book"],
    )


def _jpeg_bytes():
    image = Image.new("RGB", (40, 60), (120, 100, 90))
    out = BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


def test_explicit_fresh_scene_drops_stale_activity_pose_support_camera_but_keeps_identity_style():
    intent = v2.parse_image_intent(v2.normalize_request_v2("حالا با همین چهره یه عکس معمولی از خودت توی یه کافه دنج بده"))
    intent.scene.scene_key = "cafe"
    intent.scene.location = "cafe"
    intent.scene.explicit_current_request = True

    merged = runtime._runtime_merge_image_intent(
        intent,
        _source_plan(),
        recent_context=[],
        memory_context=[],
        routine_context={},
    )

    assert merged["scene"].value == "cafe"
    assert merged["activity"].value is None
    assert merged["pose"].value is None
    assert merged["support_surface"].value is None
    assert merged["camera"].value is None
    assert merged["wardrobe"].value == "black formal outfit"


def test_full_body_mirror_after_cafe_does_not_fail_from_stale_chair_or_camera():
    request = "یه عکس تمام‌قد جلوی آینه توی اتاقت بده"
    intent = v2.parse_image_intent(v2.normalize_request_v2(request))
    intent.scene.scene_key = "mirror"
    intent.scene.location = "mirror"
    intent.scene.explicit_current_request = True
    assert intent.composition.camera == "mirror_selfie"
    assert intent.composition.framing == "full_body"

    merged = runtime._runtime_merge_image_intent(
        intent,
        _source_plan(),
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    source_job = SimpleNamespace(id=12, seed=98765, final_provider_seed=98765, user_id=1, chat_id=1)
    plan = v2.construct_resolved_plan(
        intent,
        merged,
        v2.SafetyDecision(v2.PolicyDecision.ALLOW),
        _profile(),
        source_job=source_job,
        message_id=99,
        user_request=request,
    )
    errors = v2.validate_plan_invariants(plan, source_job=source_job, user_id=1, chat_id=1)

    assert plan.camera.value == "mirror_selfie"
    assert plan.support_surface.value is None
    assert "support_surface_scene_mismatch" not in errors
    assert "pose_support_surface_mismatch" not in errors
    assert errors == []


def _rooftop_requirements():
    return {
        "partner_visible": True,
        "environment_visibility_required": True,
        "visibility_targets": {"environment_visible": True},
        "must_satisfy": {"required_scene_elements": ["rooftop at night", "city lights"]},
        "photo_contract": {"scene_context_summary": "rooftop of a building at night"},
    }


def test_rooftop_cannot_pass_on_city_lights_only_even_when_reviewer_claims_match():
    async def reviewer(image_bytes, *, prompt, model):
        return {
            "scene_matches_request": True,
            "detected_scene": "urban night scene",
            "required_scene_evidence": ["city lights", "buildings"],
            "contradictory_scene_evidence": [],
            "confidence": "high",
        }

    result = asyncio.run(
        quality.enforce_strict_partner_scene_guard(
            b"candidate",
            _qa(),
            visual_requirements=_rooftop_requirements(),
            analyzer=reviewer,
        )
    )
    assert result.passed is False
    assert "wrong_scene" in result.reason_codes


def test_rooftop_passes_with_actual_roof_structure():
    async def reviewer(image_bytes, *, prompt, model):
        return {
            "scene_matches_request": True,
            "detected_scene": "building rooftop at night",
            "required_scene_evidence": ["roof surface", "parapet", "city skyline"],
            "contradictory_scene_evidence": [],
            "confidence": "high",
        }

    result = asyncio.run(
        quality.enforce_strict_partner_scene_guard(
            b"candidate",
            _qa(),
            visual_requirements=_rooftop_requirements(),
            analyzer=reviewer,
        )
    )
    assert result.passed is True
    assert result.strict_scene_guard_passed is True


def test_followup_identity_guard_rejects_different_face():
    async def reviewer(image_bytes, *, prompt, model):
        assert "LEFT image" in prompt and "RIGHT image" in prompt
        return {
            "same_identity": False,
            "confidence": "high",
            "matching_identity_cues": ["hair color"],
            "conflicting_identity_cues": ["different eye spacing", "different jaw/chin"],
        }

    result = asyncio.run(
        quality.enforce_strict_partner_identity_guard(
            _jpeg_bytes(),
            _jpeg_bytes(),
            _qa(),
            analyzer=reviewer,
        )
    )
    assert result.passed is False
    assert "identity_inconsistent" in result.reason_codes


def test_followup_identity_guard_accepts_same_face_with_scene_change():
    async def reviewer(image_bytes, *, prompt, model):
        return {
            "same_identity": True,
            "confidence": "high",
            "matching_identity_cues": ["eye spacing", "nose geometry", "jaw/chin"],
            "conflicting_identity_cues": [],
        }

    result = asyncio.run(
        quality.enforce_strict_partner_identity_guard(
            _jpeg_bytes(),
            _jpeg_bytes(),
            _qa(),
            analyzer=reviewer,
        )
    )
    assert result.passed is True
    assert result.strict_identity_guard_passed is True


def test_variation_does_not_soften_unrequested_book(monkeypatch):
    captured = {}
    job = SimpleNamespace(
        image_action="variation",
        source_image_job_id=None,
        metadata_json={
            "route_action": "variation",
            "expected_subject_count": 1,
            "identity_descriptor": {"face": "stable fictional face"},
            "visual_requirements": {"partner_visible": True},
        },
    )

    async def reviewer(*args, **kwargs):
        return _qa("unrequested_foreground_object")

    async def fake_core_process_job(db, received_job, *, image_client=None, telegram_service=None, generated_image_qa_evaluator=None):
        captured["qa"] = await generated_image_qa_evaluator(b"candidate")
        return "done"

    monkeypatch.setattr(base, "partner_identity_generation_required", lambda metadata: True)
    monkeypatch.setattr(base, "process_job", fake_core_process_job)

    outcome = asyncio.run(
        runtime.process_job(None, job, generated_image_qa_evaluator=reviewer)
    )
    assert outcome == "done"
    assert captured["qa"].passed is False
    assert captured["qa"].reason_codes == ["unrequested_foreground_object"]
