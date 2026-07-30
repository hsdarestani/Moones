from datetime import datetime, timedelta
from types import SimpleNamespace


def test_adult_body_correction_routes_to_image():
    from app.services.semantic_image_intent_router import SemanticImageAction, canonical_explicit_image_action
    assert canonical_explicit_image_action("نه لختی میخوام. ممه هات معلوم باشه") == SemanticImageAction.GENERATE_NEW
    assert canonical_explicit_image_action("خب بفرس عکس ممه هاتو") == SemanticImageAction.GENERATE_NEW


def test_deterministic_adult_guard_preserves_full_nudity_and_topless():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import apply_deterministic_adult_visual_intent
    nude=v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))
    nude=apply_deterministic_adult_visual_intent(nude, "یه عکس لختی بده بدنتو ببینم")
    assert nude.content_classification == v2.ContentClassification.FULL_NUDITY
    assert nude.adult_intent == "full_nudity"
    topless=v2.parse_image_intent(v2.normalize_request_v2("خب بفرس عکس ممه هاتو"))
    topless=apply_deterministic_adult_visual_intent(topless, "خب بفرس عکس ممه هاتو")
    assert topless.content_classification == v2.ContentClassification.TOPLESS
    assert topless.body_visibility.regions["breasts"].visibility_requested is True


def test_topless_and_full_nudity_use_adult_model():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import select_generation_model
    for classification in (v2.ContentClassification.TOPLESS, v2.ContentClassification.FULL_NUDITY):
        assert select_generation_model(content_classification=classification, default_model="seedream-v5-lite", adult_model="lustify-sdxl") == "lustify-sdxl"


def test_adult_visibility_is_forced_out_of_public_routine_scene():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import apply_adult_scene_policy, apply_deterministic_adult_visual_intent
    intent=v2.parse_image_intent(v2.normalize_request_v2("خب بفرس عکس ممه هاتو"))
    intent=apply_deterministic_adult_visual_intent(intent, "خب بفرس عکس ممه هاتو")
    result=apply_adult_scene_policy(intent, {"location":"street", "scene":"street"})
    assert result.private_scene_applied is True
    assert intent.scene.scene_key == "private_indoor"
    assert intent.scene.privacy == "private"
    assert result.routine_context["location"] is None


def test_refinement_uses_sent_plan_without_exact_bytes_but_resend_does_not():
    from app.services import image_pipeline_v2 as v2
    from app.services.semantic_image_intent_router import SemanticImageAction, SemanticImageDecision, validate_source_reference_deterministically
    job=SimpleNamespace(user_id=1, chat_id=2, status="sent", sent_at=datetime.utcnow()-timedelta(minutes=5), resolved_plan_json={"plan_version":v2.PLAN_VERSION}, metadata_json={}, artifacts=[SimpleNamespace(image_bytes=None)])
    assert v2.source_job_is_context_eligible(job, user_id=1, chat_id=2) is True
    assert v2.source_job_is_retrievable(job, user_id=1, chat_id=2) is False
    refine=SemanticImageDecision(action=SemanticImageAction.REFINE_PREVIOUS, media_delivery_requested=True, confidence=1, reason_code="test")
    resend=SemanticImageDecision(action=SemanticImageAction.RESEND_EXACT, media_delivery_requested=True, confidence=1, reason_code="test")
    assert validate_source_reference_deterministically(refine, recent_retrievable_image_exists=True, recent_source_image_exists=True, recent_exact_artifact_exists=False, allowed_job_ids=set()) == (True, None)
    assert validate_source_reference_deterministically(resend, recent_retrievable_image_exists=True, recent_source_image_exists=True, recent_exact_artifact_exists=False, allowed_job_ids=set())[0] is False


def test_recent_denied_cafe_request_supplies_scene_to_short_full_body_followup():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_service import inherit_recent_image_scene
    current=v2.parse_image_intent(v2.normalize_request_v2("یه عکس قدی بده"))
    recent=[SimpleNamespace(role="user", content="همین قبلی رو تو کافه بده")]
    current=inherit_recent_image_scene(current, recent)
    assert current.scene.scene_key == "cafe" or current.scene.location == "cafe"
    assert current.photo_contract["current_scene_from_chat"] is True


def test_delivered_artifact_is_retained_for_continuity():
    from app.services.image_generation_service import mark_delivered_artifact_retained
    job=SimpleNamespace(metadata_json={})
    artifact=SimpleNamespace(cleared_at=datetime.utcnow(), image_bytes=b"image")
    mark_delivered_artifact_retained(job, artifact, retention_hours=6)
    assert artifact.image_bytes == b"image"
    assert artifact.cleared_at is None
    assert job.metadata_json["artifact_retained_for_continuity"] is True
