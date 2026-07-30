def _decision(action="generate_new"):
    from app.services.semantic_image_intent_router import SemanticImageDecision, VisualIntent
    return SemanticImageDecision(action=action,media_delivery_requested=True,confidence=1.0,reason_code="test",needs_clarification=False,visual_intent=VisualIntent(primary_subject="partner",partner_visible=True))


def test_previous_reference_is_locked_to_latest_sent_job():
    from app.services.semantic_image_intent_router import RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext, enforce_previous_image_and_scene_continuity
    context=SemanticImageRouterContext(current_user_message="همین قبلی رو تو کافه بده",recent_image_job=RecentImageJobSummary(job_id=77,status="sent",has_retrievable_artifact=True))
    result=enforce_previous_image_and_scene_continuity(context,context.current_user_message,_decision(SemanticImageAction.REFINE_PREVIOUS))
    assert result.source_reference.job_id == 77
    assert result.visual_intent.scene == "cafe"
    assert result.visual_intent.scene_explicit_current_request is True


def test_future_home_transition_requires_arrival_then_becomes_current_scene():
    from app.services.semantic_image_intent_router import ConversationTurnSummary, SemanticImageRouterContext, enforce_previous_image_and_scene_continuity
    context=SemanticImageRouterContext(current_user_message="خب بفرست عکس همه هاتو",recent_conversation=[ConversationTurnSummary(role="assistant",text_summary="آروم میرسم خونه"),ConversationTurnSummary(role="user",text_summary="رسیدی فک کنم")])
    result=enforce_previous_image_and_scene_continuity(context,context.current_user_message,_decision())
    assert result.visual_intent.location == "home"
    assert result.visual_intent.environment_type == "private_indoor"
    assert result.visual_intent.current_scene_from_chat is True


def test_full_nudity_intent_exports_required_body_regions():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import apply_deterministic_adult_visual_intent
    intent=v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))
    intent=apply_deterministic_adult_visual_intent(intent,"یه عکس لختی بده بدنتو ببینم")
    assert intent.content_classification == v2.ContentClassification.FULL_NUDITY
    vr=v2.resolve_visual_requirements(intent,user_request="یه عکس لختی بده بدنتو ببینم")
    assert {"breasts","buttocks","full_body"}.issubset(set(vr.required_body_regions))


def test_qa_rejects_clothed_result_for_explicit_nudity():
    from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload
    payload={"person_count":1,"face_count":1,"intended_subject_count":1,"confidence":"high","framing":"full_body","framing_matches_request":True,"head_inside_frame":True,"feet_inside_frame":True,"body_not_cropped":True,"requested_scene_visible":True,"requested_support_surface_visible":True,"requested_pose_matches":True,"identity_consistency_reasonable":True,"primary_subject_matches_request":True,"partner_visible":True,"face_visible":True,"camera_mode_matches_request":True,"natural_capture_plausible":True,"looks_like_id_photo":False,"requested_nudity_visible":False,"required_body_regions_visible":False,"reason_codes":[]}
    vr={"explicit_nudity_requested":True,"required_body_regions":["breasts","buttocks","full_body"],"framing_requirement":"full_body","full_body_visible":True,"photo_contract":{"partner_visible":True,"natural_capture_required":True}}
    result=evaluate_generated_image_composition_payload(payload,expected_subject_count=1,visual_requirements=vr)
    assert result.passed is False
    assert "requested_nudity_missing" in result.reason_codes
    assert "requested_body_region_missing" in result.reason_codes
