import asyncio
from types import SimpleNamespace


def test_generated_qa_uses_at_most_two_vision_calls(monkeypatch):
    import app.services.generated_image_qa_service as qa
    calls=[]
    async def fail(*args, **kwargs):
        calls.append(kwargs.get("model"))
        raise RuntimeError("vision down")
    monkeypatch.setattr(qa, "analyze_image_bytes_with_venice", fail)
    monkeypatch.setattr(qa, "get_settings", lambda: SimpleNamespace(venice_api_key="x", vision_model="primary", vision_fallback_model="fallback"))
    result=asyncio.run(qa.evaluate_generated_image_composition(b"image", expected_subject_count=1))
    assert result.passed is False
    assert "qa_provider_failure" in result.reason_codes
    assert calls == ["primary", "fallback"]


def test_normal_image_can_degrade_on_pure_qa_outage():
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    from app.services.image_generation_service import generated_image_qa_can_degrade, accept_degraded_generated_image_qa
    job=SimpleNamespace(metadata_json={"visual_requirements":{"anatomy_qa_required":False,"explicit_nudity_requested":False}})
    result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,"low",["qa_provider_failure","qa_uncertain"],None)
    assert generated_image_qa_can_degrade(job, result) is True
    accepted=accept_degraded_generated_image_qa(result)
    assert accepted.passed is True
    assert accepted.reason_codes == []
    assert accepted.raw_provider_reason_codes == ["qa_provider_failure","qa_uncertain"]


def test_adult_anatomy_qa_never_degrades():
    from app.services.generated_image_qa_service import GeneratedImageQAResult
    from app.services.image_generation_service import generated_image_qa_can_degrade
    job=SimpleNamespace(metadata_json={"visual_requirements":{"anatomy_qa_required":True,"explicit_nudity_requested":True}})
    result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,"low",["qa_provider_failure","qa_uncertain"],None)
    assert generated_image_qa_can_degrade(job, result) is False


def test_colloquial_active_job_followup_gets_semantic_status():
    from app.services.semantic_image_intent_router import (
        SemanticImageDecision, SemanticImageAction, SemanticImageRouterContext,
        RecentImageJobSummary, resolve_active_image_job_followup_semantically,
    )
    class Client:
        async def complete_result(self, *args, **kwargs):
            return SimpleNamespace(text='{"action":"status_query","confidence":0.96}')
    model=SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    context=SemanticImageRouterContext(
        current_user_message="چیشد خبیب",
        active_image_job=RecentImageJobSummary(job_id=12,status="processing",action="generate_new"),
    )
    initial=SemanticImageDecision(action=SemanticImageAction.CHAT,media_delivery_requested=False,confidence=.8,reason_code="chat")
    resolved=asyncio.run(resolve_active_image_job_followup_semantically(context, initial, model=model))
    assert resolved.action == SemanticImageAction.STATUS_QUERY


def test_new_request_is_absorbed_while_job_active():
    from app.services.semantic_image_intent_router import (
        SemanticImageDecision, SemanticImageAction, SemanticImageRouterContext,
        RecentImageJobSummary, should_report_active_job_instead_of_enqueuing,
    )
    context=SemanticImageRouterContext(current_user_message="عکس بده ببینمت",active_image_job=RecentImageJobSummary(job_id=3,status="queued"))
    decision=SemanticImageDecision(action=SemanticImageAction.GENERATE_NEW,media_delivery_requested=True,confidence=1,reason_code="clear")
    assert should_report_active_job_instead_of_enqueuing(context, decision) is True


def test_stale_sent_job_does_not_trigger_second_control_model():
    from app.services.semantic_image_intent_router import (
        SemanticImageDecision, SemanticImageAction, SemanticImageRouterContext,
        RecentImageJobSummary, resolve_active_image_job_followup_semantically,
    )
    class Client:
        async def complete_result(self, *args, **kwargs):
            raise AssertionError("control model must not run for a stale image")
    model=SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    context=SemanticImageRouterContext(
        current_user_message="امروز چه خبر",
        latest_image_job=RecentImageJobSummary(job_id=99,status="sent",action="generate_new"),
        seconds_since_recent_image=3600,
    )
    initial=SemanticImageDecision(action=SemanticImageAction.CHAT,media_delivery_requested=False,confidence=.9,reason_code="chat")
    resolved=asyncio.run(resolve_active_image_job_followup_semantically(context, initial, model=model))
    assert resolved is initial
