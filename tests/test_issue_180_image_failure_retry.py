from datetime import datetime
from types import SimpleNamespace


def _decision(action):
    from app.services.semantic_image_intent_router import SemanticImageDecision
    return SemanticImageDecision(action=action, media_delivery_requested=action != "chat", confidence=.9, reason_code="test")


def test_relative_previous_falls_back_to_actual_sent_job_when_latest_failed():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext,
        enforce_relative_previous_image_reference,
    )
    context=SemanticImageRouterContext(
        current_user_message="همین قبلی رو تو کافه بده",
        recent_image_job=RecentImageJobSummary(job_id=41,status="sent"),
        latest_image_job=RecentImageJobSummary(job_id=42,status="failed"),
    )
    result=enforce_relative_previous_image_reference(context,_decision(SemanticImageAction.GENERATE_NEW))
    assert result.action == SemanticImageAction.REFINE_PREVIOUS
    assert result.source_reference.job_id == 41


def test_failed_contract_merge_keeps_old_full_body_and_new_cafe_scene():
    from app.services.semantic_image_router_context import merge_failed_image_retry_contract
    old=SimpleNamespace(
        user_request="یه عکس قدی بده",
        metadata_json={},
        resolved_plan_json={
            "composition":{"framing":"full_body"},
            "visual_requirements":{"framing_requirement":"full_body","full_body_visible":True,"required_body_regions":["full_body"],"photo_contract":{"camera_mode":"mirror_selfie","partner_visible":True}},
            "scene":{"value":"home"},
        },
    )
    new=SimpleNamespace(
        user_request="همین قبلی رو تو کافه بده",
        metadata_json={},
        resolved_plan_json={
            "scene":{"value":"cafe"},
            "location":{"value":"cafe"},
            "visual_requirements":{"photo_contract":{"partner_visible":True}},
        },
    )
    text, visual=merge_failed_image_retry_contract([new, old])
    assert text == "یه عکس قدی بده؛ سپس همین قبلی رو تو کافه بده"
    assert visual["scene"] == "cafe"
    assert visual["framing"] == "full_body"
    assert "full_body" in visual["required_body_regions"]
    assert visual["camera_mode"] == "mirror_selfie"


def test_short_photo_command_retries_recent_failed_contract_exactly():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext,
        enforce_recent_failed_image_retry,
    )
    context=SemanticImageRouterContext(
        current_user_message="عکس بده",
        latest_image_job=RecentImageJobSummary(
            job_id=52, status="failed", failed_at=datetime.utcnow().isoformat(),
            retry_request_text="یه عکس قدی بده؛ سپس همین قبلی رو تو کافه بده",
            retry_visual_intent={"scene":"cafe","location":"cafe","framing":"full_body","camera_mode":"mirror_selfie","required_body_regions":["full_body"]},
        ),
    )
    result=enforce_recent_failed_image_retry(context,_decision(SemanticImageAction.GENERATE_NEW))
    assert result.action == SemanticImageAction.GENERATE_NEW
    assert result.reason_code == "recent_failed_image_contract_retry"
    assert result.retry_request_text.startswith("یه عکس قدی بده")
    assert result.visual_intent.scene == "cafe"
    assert result.visual_intent.framing == "full_body"


def test_ordinary_chat_after_failure_gets_non_hallucination_grounding():
    from app.engine.simple_chat import failed_image_grounding_block
    block=failed_image_grounding_block({"image_job_grounding":{"status":"failed","job_id":52}})
    assert "most recent image request failed" in block
    assert "Never claim" in block
    assert failed_image_grounding_block({}) == ""


def test_failed_contract_merge_does_not_pull_unrelated_older_request():
    from app.services.semantic_image_router_context import merge_failed_image_retry_contract
    latest=SimpleNamespace(user_request="یه عکس تو پارک بده", metadata_json={}, resolved_plan_json={"scene":{"value":"park"}})
    older=SimpleNamespace(user_request="یه عکس قدی بده", metadata_json={}, resolved_plan_json={"composition":{"framing":"full_body"}})
    text, visual=merge_failed_image_retry_contract([latest])
    assert text == "یه عکس تو پارک بده"
    assert visual["scene"] == "park"
    assert "framing" not in visual
