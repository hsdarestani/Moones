import asyncio
from types import SimpleNamespace


def test_explicit_persian_nudity_is_locked_and_private_scene_clears_cafe_contract():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import (
        apply_adult_scene_policy,
        apply_deterministic_adult_visual_intent,
    )

    intent = v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))
    intent = apply_deterministic_adult_visual_intent(intent, "یه عکس لختی بده بدنتو ببینم")
    intent.photo_contract = {
        "current_scene_from_chat": True,
        "scene_context_summary": "the partner is outside at a cafe",
    }
    intent.passthrough_visual_details = ["current scene and activity: the partner is outside at a cafe"]
    result = apply_adult_scene_policy(intent, {"location": "cafe", "scene": "street"})

    assert str(intent.content_classification) == str(v2.ContentClassification.FULL_NUDITY)
    assert result.private_scene_applied is True
    assert intent.scene.scene_key == "private_indoor"
    assert intent.scene.privacy == "private"
    assert intent.photo_contract["current_scene_from_chat"] is False
    assert intent.photo_contract["scene_context_summary"] is None
    assert not intent.passthrough_visual_details


def test_body_referential_followup_inherits_recent_explicit_adult_request_only():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import inherit_recent_adult_visual_intent

    intent = v2.parse_image_intent(v2.normalize_request_v2("خب بفرس عکس همه هاتو"))
    recent = [SimpleNamespace(role="user", content="یه عکس لختی بده بدنتو ببینم")]
    inherited = inherit_recent_adult_visual_intent(intent, "خب بفرس عکس همه هاتو", recent)
    assert str(inherited.content_classification) == str(v2.ContentClassification.FULL_NUDITY)

    ordinary = v2.parse_image_intent(v2.normalize_request_v2("یه عکس قدی بده"))
    ordinary = inherit_recent_adult_visual_intent(ordinary, "یه عکس قدی بده", recent)
    assert str(ordinary.content_classification) != str(v2.ContentClassification.FULL_NUDITY)


def test_full_nudity_prompt_forbids_clothed_fallback():
    from app.services import image_pipeline_v2 as v2

    vr = v2.VisualRequirements(
        explicit_nudity_requested=True,
        anatomy_consistency_required=True,
        anatomical_profile="female",
        framing_requirement="full_body",
        full_body_visible=True,
        head_visible=True,
        feet_visible=True,
        body_not_cropped=True,
        photo_contract={"partner_visible": True, "identity_consistency_required": True},
    )
    plan = SimpleNamespace(
        identity={"descriptor": {}},
        current_intent={"content_classification": "full_nudity", "expression_modifiers": [], "explicit_exclusions": []},
        body_visibility={"full_body": {"visibility_requested": True, "framing_requested": True}},
        composition={"expected_subject_count": 1, "photo_contract": vr.photo_contract, "width": 1024, "height": 1280},
        visual_requirements=vr,
        continuity_plan=v2.ContinuityPlan(),
        action=v2.ImageAction.NEW_GENERATION,
        scene=v2.ResolvedField("private_indoor", v2.Provenance.EXPLICIT),
        location=v2.ResolvedField("private indoor setting", v2.Provenance.EXPLICIT),
        activity=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        pose=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        support_surface=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        wardrobe=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        camera=v2.ResolvedField("casual_selfie", v2.Provenance.EXPLICIT),
        lighting=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        required_objects=v2.ResolvedField([], v2.Provenance.SYSTEM),
        excluded_objects=v2.ResolvedField([], v2.Provenance.SYSTEM),
        passthrough_visual_details=[],
        seed_strategy={"final_provider_seed": 7},
    )
    compiled = v2.compile_image_prompt(plan)
    assert "Hard fulfillment requirement: the fictional adult is visibly fully nude" in compiled.positive_prompt
    for term in ("clothed body", "shirt", "underwear", "covered torso"):
        assert term in compiled.negative_prompt


def _base_qa_payload(**updates):
    payload = {
        "person_count": 1,
        "face_count": 1,
        "intended_subject_count": 1,
        "confidence": "high",
        "framing": "full_body",
        "framing_matches_request": True,
        "full_body_visible": True,
        "head_inside_frame": True,
        "feet_inside_frame": True,
        "body_not_cropped": True,
        "requested_scene_visible": True,
        "requested_support_surface_visible": True,
        "requested_pose_matches": True,
        "requested_nudity_visible": False,
        "natural_capture_plausible": True,
        "looks_like_id_photo": False,
        "reason_codes": [],
    }
    payload.update(updates)
    return payload


def test_clothed_output_cannot_pass_explicit_nudity_qa_or_delivery_gate():
    from app.services.generated_image_qa_service import (
        evaluate_generated_image_composition_payload,
        metadata_has_valid_generated_image_qa,
    )

    requirements = {
        "explicit_nudity_requested": True,
        "framing_requirement": "full_body",
        "full_body_visible": True,
        "photo_contract": {},
    }
    result = evaluate_generated_image_composition_payload(
        _base_qa_payload(),
        expected_subject_count=1,
        visual_requirements=requirements,
    )
    assert result.passed is False
    assert "requested_nudity_missing" in result.reason_codes

    image = b"candidate"
    metadata = {
        "visual_requirements": requirements,
        "generated_image_qa": {
            **result.to_metadata(artifact_checksum=__import__("hashlib").sha256(image).hexdigest()),
            "passed": True,
            "requested_nudity_visible": False,
        },
    }
    assert metadata_has_valid_generated_image_qa(metadata, image) is False


def test_arrival_chat_is_not_reclassified_as_image_status_and_costs_no_control_call():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary,
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        VisualIntent,
        resolve_active_image_job_followup_semantically,
    )

    class Client:
        async def complete_result(self, *args, **kwargs):
            raise AssertionError("status control model must not be called")

    model = SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    context = SemanticImageRouterContext(
        current_user_message="رسیدی فک کنم",
        latest_image_job=RecentImageJobSummary(job_id=1, status="sent"),
        seconds_since_recent_image=30,
    )
    original = SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=.9,
        reason_code="ordinary_world_state_chat",
        visual_intent=VisualIntent(),
    )
    result = asyncio.run(resolve_active_image_job_followup_semantically(context, original, model=model))
    assert result is original
    assert result.action == SemanticImageAction.CHAT


def test_colloquial_what_happened_still_reaches_image_status_control():
    from app.services.semantic_image_intent_router import image_job_followup_candidate

    assert image_job_followup_candidate("چیشد پس") is True
    assert image_job_followup_candidate("عکس کو") is True
    assert image_job_followup_candidate("رسیدی فک کنم") is False


def test_relative_previous_image_with_cafe_change_uses_latest_as_refinement():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary,
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        SemanticSourceReference,
        VisualIntent,
        enforce_relative_previous_image_reference,
    )

    context = SemanticImageRouterContext(
        current_user_message="همین قبلی رو تو کافه بده",
        latest_image_job=RecentImageJobSummary(job_id=42, status="sent"),
    )
    decision = SemanticImageDecision(
        action=SemanticImageAction.RESEND_EXACT,
        media_delivery_requested=True,
        confidence=.9,
        reason_code="model_exact_resend",
        source_reference=SemanticSourceReference(kind="image_job", job_id=7),
        visual_intent=VisualIntent(location="cafe"),
    )
    result = enforce_relative_previous_image_reference(context, decision)
    assert result.action == SemanticImageAction.REFINE_PREVIOUS
    assert result.source_reference.job_id == 42
