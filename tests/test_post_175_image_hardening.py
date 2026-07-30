def _decision(action):
    from app.services.semantic_image_intent_router import SemanticImageDecision
    return SemanticImageDecision(action=action, media_delivery_requested=action != "chat", confidence=.9, reason_code="test", needs_clarification=action == "clarify")


def test_previous_image_reference_recovers_wrong_generate_new_action():
    from app.services.semantic_image_intent_router import RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext, enforce_relative_previous_image_reference
    context=SemanticImageRouterContext(current_user_message="همین قبلی رو تو کافه بده", recent_image_job=RecentImageJobSummary(job_id=91,status="sent",has_retrievable_artifact=False))
    result=enforce_relative_previous_image_reference(context,_decision(SemanticImageAction.GENERATE_NEW))
    assert result.action == SemanticImageAction.REFINE_PREVIOUS
    assert result.source_reference.job_id == 91
    assert result.media_delivery_requested is True
    assert result.needs_clarification is False


def test_nudity_body_regions_reach_visual_requirements():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import apply_deterministic_adult_visual_intent
    intent=v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))
    intent=apply_deterministic_adult_visual_intent(intent,"یه عکس لختی بده بدنتو ببینم")
    requirements=v2.resolve_visual_requirements(intent,user_request="یه عکس لختی بده بدنتو ببینم")
    assert {"breasts","buttocks","full_body"}.issubset(set(requirements.required_body_regions))
    assert {"breasts","buttocks","full_body"}.issubset(set(requirements.must_satisfy["required_body_regions"]))


def test_primary_and_compact_qa_require_pixel_evidence():
    from app.services.generated_image_qa_service import _qa_prompt_with_requirements, _compact_qa_prompt_with_requirements
    requirements={"explicit_nudity_requested":True,"required_body_regions":["breasts","buttocks","full_body"]}
    primary=_qa_prompt_with_requirements(requirements)
    compact=_compact_qa_prompt_with_requirements(requirements,expected_subject_count=1,expected_interaction=None)
    for prompt in (primary, compact):
        assert "actual pixels" in prompt
        assert "requested_body_regions" in prompt
        assert "example schema" in prompt


def test_requested_nudity_failure_gets_specific_retry_constraint():
    from app.services.generated_image_qa_service import corrective_prompt_for_reasons
    correction=corrective_prompt_for_reasons(["requested_nudity_missing"])
    assert "clothed" in correction
    assert "requested body region" in correction
