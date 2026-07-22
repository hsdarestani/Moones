import asyncio
from types import SimpleNamespace

import app.services.generated_image_qa_service as qa_service
from app.services.semantic_image_intent_router import (
    ConversationTurnSummary, SemanticImageAction, SemanticImageDecision,
    SemanticImageRouterContext, VisualIntent, enforce_partner_photo_defaults,
)
from app.services.generated_image_qa_service import (
    evaluate_generated_image_composition_payload,
    metadata_has_valid_generated_image_qa,
)
from app.services.image_generation_service import suppress_routine_scene_for_current_chat_scene
from app.services.partner_photo_contract import build_partner_photo_contract, prompt_constraints


def _context():
    return SemanticImageRouterContext(
        current_user_message="یه عکس بده از الانت",
        recent_conversation=[
            ConversationTurnSummary(role="assistant", text_summary="تازه رسیدم خونه و روی مبل نشستم")
        ],
    )


def test_generic_partner_photo_is_selfie_first_and_uses_current_scene_context():
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,
        confidence=0.95, reason_code="direct_photo", visual_intent=VisualIntent(location="home", activity="sitting on the sofa"),
    )
    fixed = enforce_partner_photo_defaults(_context(), decision)
    assert fixed.visual_intent.camera_mode == "casual_selfie"
    assert fixed.visual_intent.face_visible is True
    assert fixed.visual_intent.current_scene_from_chat is True
    assert "sitting on the sofa" in fixed.visual_intent.scene_context_summary
    contract = build_partner_photo_contract(fixed.visual_intent)
    assert contract["camera_mode"] == "casual_selfie"
    assert contract["identity_consistency_required"] is True
    assert contract["current_scene_from_chat"] is True


def test_full_body_partner_photo_defaults_to_mirror_selfie():
    vi = VisualIntent(primary_subject="partner", framing="full_body")
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,
        confidence=0.95, reason_code="direct_photo", visual_intent=vi,
    )
    fixed = enforce_partner_photo_defaults(_context(), decision)
    assert fixed.visual_intent.camera_mode == "mirror_selfie"


def test_object_photo_is_not_forced_into_selfie():
    vi = VisualIntent(primary_subject="object", object_only=True, partner_visible=False)
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,
        confidence=0.95, reason_code="object_photo", visual_intent=vi,
    )
    fixed = enforce_partner_photo_defaults(_context(), decision)
    assert fixed.visual_intent.camera_mode is None


def _qa_payload(**overrides):
    payload = {
        "person_count": 1, "face_count": 1, "intended_subject_count": 1,
        "unexpected_additional_person_visible": False, "background_extra_person_visible": False,
        "duplicate_subject_visible": False, "reflection_visible": False,
        "selfie_detected": True, "mirror_selfie_detected": False,
        "confidence": "high", "framing": "medium", "framing_matches_request": True,
        "requested_scene_visible": True, "identity_consistency_reasonable": True,
        "primary_subject_matches_request": True, "partner_visible": True, "face_visible": True,
        "camera_mode_matches_request": True, "camera_source_geometry_consistent": True,
        "selfie_lens_perspective_plausible": True, "third_person_viewpoint_detected": False,
        "visible_held_phone_detected": False, "natural_capture_plausible": True,
        "looks_like_id_photo": False, "reason_codes": [],
    }
    payload.update(overrides)
    return payload


def _requirements():
    contract = {
        "primary_subject": "partner", "partner_visible": True, "camera_mode": "casual_selfie",
        "natural_capture_required": True, "identity_visibility_scope": "full",
        "identity_consistency_required": True, "identity_anchor": {"gender_presentation": "feminine", "hair": "dark wavy hair"},
        "current_scene_from_chat": True, "scene_context_summary": "at home on the sofa",
    }
    return {
        "requested_action": "new_generation", "environment_visibility_required": True,
        "framing_requirement": "natural_medium_or_medium_wide", "photo_contract": contract,
        "must_satisfy": {"required_scene_elements": ["at home on the sofa"]},
    }


def test_staged_third_person_portrait_fails_selfie_requirement():
    result = evaluate_generated_image_composition_payload(
        _qa_payload(selfie_detected=False, camera_mode_matches_request=False),
        expected_subject_count=1, selfie_allowed=True, visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "selfie_required" in result.reason_codes


def test_identity_and_current_scene_are_fail_closed():
    result = evaluate_generated_image_composition_payload(
        _qa_payload(identity_consistency_reasonable=None, requested_scene_visible=False),
        expected_subject_count=1, selfie_allowed=True, visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "identity_inconsistent" in result.reason_codes
    assert "wrong_scene" in result.reason_codes


def test_non_scene_assistant_message_is_not_forced_into_photo_scene():
    context = SemanticImageRouterContext(
        current_user_message="یه عکس بده",
        recent_conversation=[ConversationTurnSummary(role="assistant", text_summary="از بخش افزودنی‌ها می‌تونی قابلیت‌ها رو فعال کنی")],
    )
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,
        confidence=0.95, reason_code="direct_photo", visual_intent=VisualIntent(),
    )
    fixed = enforce_partner_photo_defaults(context, decision)
    assert fixed.visual_intent.current_scene_from_chat is False
    assert fixed.visual_intent.scene_context_summary is None


def test_generic_model_camera_default_is_overridden_but_explicit_timer_is_preserved():
    generic = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True, confidence=0.95,
        reason_code="direct_photo", visual_intent=VisualIntent(camera_mode="casual_phone_photo"),
    )
    assert enforce_partner_photo_defaults(_context(), generic).visual_intent.camera_mode == "casual_selfie"
    explicit = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True, confidence=0.95,
        reason_code="direct_photo", visual_intent=VisualIntent(camera_mode="tripod_timer", camera_explicit_current_request=True),
    )
    assert enforce_partner_photo_defaults(_context(), explicit).visual_intent.camera_mode == "tripod_timer"


def test_model_provided_current_scene_summary_survives_without_canonical_fields():
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,
        confidence=0.95, reason_code="direct_photo",
        visual_intent=VisualIntent(
            current_scene_from_chat=True,
            scene_context_summary="at home, lying on the sofa",
        ),
    )
    fixed = enforce_partner_photo_defaults(_context(), decision)
    assert fixed.visual_intent.current_scene_from_chat is True
    assert fixed.visual_intent.scene_context_summary == "at home, lying on the sofa"
    assert any("lying on the sofa" in item for item in fixed.visual_intent.freeform_visual_constraints)


def test_conversation_scene_removes_conflicting_routine_location():
    routine = {"location": "outdoor street", "slot_name": "afternoon", "environment_type": "public_outdoor"}
    contract = {"current_scene_from_chat": True, "scene_context_summary": "at home, lying on the sofa"}
    cleaned = suppress_routine_scene_for_current_chat_scene(routine, contract)
    assert cleaned["location"] is None
    assert cleaned["environment_type"] is None
    assert cleaned["slot_name"] == "afternoon"
    assert routine["location"] == "outdoor street"


def test_non_mirror_selfie_with_visible_phone_and_external_viewpoint_is_rejected():
    result = evaluate_generated_image_composition_payload(
        _qa_payload(
            selfie_detected=True,
            camera_mode_matches_request=True,
            camera_source_geometry_consistent=False,
            selfie_lens_perspective_plausible=False,
            third_person_viewpoint_detected=True,
            visible_held_phone_detected=True,
        ),
        expected_subject_count=1,
        selfie_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "selfie_geometry_inconsistent" in result.reason_codes
    assert "third_person_viewpoint" in result.reason_codes
    assert "visible_phone_in_non_mirror_selfie" in result.reason_codes


def test_valid_arm_length_selfie_geometry_passes():
    result = evaluate_generated_image_composition_payload(
        _qa_payload(),
        expected_subject_count=1,
        selfie_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is True
    assert result.camera_source_geometry_consistent is True
    assert result.third_person_viewpoint_detected is False
    assert result.visible_held_phone_detected is False


def test_casual_selfie_prompt_forbids_external_camera_and_visible_phone():
    lines = prompt_constraints({"camera_mode": "casual_selfie", "natural_capture_required": True})
    joined = " ".join(lines)
    assert "phone device itself is outside the frame" in joined
    assert "no external photographer" in joined
    assert "overhead camera" in joined


def test_checksum_bound_delivery_gate_rejects_old_or_incomplete_selfie_geometry():
    image_bytes = b"candidate-image"
    checksum = __import__("hashlib").sha256(image_bytes).hexdigest()
    metadata = {
        "generated_image_qa": {
            "passed": True,
            "artifact_checksum": checksum,
            "selfie_detected": True,
            "camera_mode_matches_request": True,
            "camera_source_geometry_consistent": None,
            "selfie_lens_perspective_plausible": None,
            "third_person_viewpoint_detected": False,
            "visible_held_phone_detected": False,
            "requested_scene_visible": True,
            "identity_consistency_reasonable": True,
            "natural_capture_plausible": True,
            "looks_like_id_photo": False,
        },
        "visual_requirements": _requirements(),
    }
    assert metadata_has_valid_generated_image_qa(metadata, image_bytes) is False


def test_legacy_qa_payload_gets_compact_retry_for_new_selfie_geometry(monkeypatch):
    legacy = _qa_payload()
    for key in (
        "camera_source_geometry_consistent",
        "selfie_lens_perspective_plausible",
        "third_person_viewpoint_detected",
        "visible_held_phone_detected",
    ):
        legacy.pop(key, None)
    complete = _qa_payload()
    calls = []

    async def fake_analyze(image_bytes, *, prompt, model):
        calls.append(prompt)
        return complete if prompt.startswith("You are a compact fail-closed") else legacy

    monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", fake_analyze)
    monkeypatch.setattr(
        qa_service,
        "get_settings",
        lambda: SimpleNamespace(venice_api_key="test", vision_model="vision-primary", vision_fallback_model=""),
    )
    result = asyncio.run(qa_service.evaluate_generated_image_composition(
        b"image-bytes",
        expected_subject_count=1,
        selfie_allowed=True,
        visual_requirements=_requirements(),
    ))
    assert result.passed is True
    assert len(calls) == 2
    assert calls[1].startswith("You are a compact fail-closed")


def test_compact_qa_retries_once_after_transient_provider_error(monkeypatch):
    complete = _qa_payload()
    call_count = {"value": 0}

    async def fake_analyze(image_bytes, *, prompt, model):
        call_count["value"] += 1
        if call_count["value"] < 3:
            raise RuntimeError("temporary vision failure")
        return complete

    monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", fake_analyze)
    monkeypatch.setattr(
        qa_service,
        "get_settings",
        lambda: SimpleNamespace(venice_api_key="test", vision_model="vision-primary", vision_fallback_model=""),
    )
    result = asyncio.run(qa_service.evaluate_generated_image_composition(
        b"image-bytes",
        expected_subject_count=1,
        selfie_allowed=True,
        visual_requirements=_requirements(),
    ))
    assert result.passed is True
    assert call_count["value"] == 3
