from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count=text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old,new,1)

router_path=Path('app/services/semantic_image_intent_router.py')
router=router_path.read_text()
old="""    target=context.active_image_job or context.latest_image_job
    if target is None or decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.CLARIFY}:
        return decision
    if str(getattr(target, 'status', '') or '') not in {'queued','processing','generating','sending','delivery_failed','failed','sent'}:
        return decision
"""
new="""    if decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.CLARIFY}:
        return decision
    target=context.active_image_job
    if target is None:
        latest=context.latest_image_job
        latest_status=str(getattr(latest, 'status', '') or '') if latest else ''
        if latest_status == 'sent' and context.seconds_since_recent_image is not None and context.seconds_since_recent_image <= 600:
            target=latest
        elif latest_status in {'failed','delivery_failed'}:
            timestamp=getattr(latest, 'failed_at', None) or getattr(latest, 'created_at', None)
            try:
                age_seconds=max(0, int((datetime.utcnow()-datetime.fromisoformat(str(timestamp))).total_seconds()))
            except Exception:
                age_seconds=10**9
            if age_seconds <= 600:
                target=latest
    if target is None:
        return decision
    if str(getattr(target, 'status', '') or '') not in {'queued','processing','generating','sending','delivery_failed','failed','sent'}:
        return decision
"""
router=replace_once(router,old,new,'fresh active job target')
router_path.write_text(router)

svc_path=Path('app/services/image_generation_service.py')
svc=svc_path.read_text()
svc=replace_once(
    svc,
    "reason_codes=['qa_provider_retry_exhausted' if job.status == 'failed' else 'qa_provider_retry_scheduled'] if qa_transient else ['provider_transport_failure']",
    "reason_codes=['qa_provider_unavailable_final'] if qa_transient else ['provider_transport_failure']",
    'QA failure reason code',
)
svc_path.write_text(svc)

test_path=Path('tests/test_image_qa_latency_status.py')
test=test_path.read_text()
test += '''\n\ndef test_stale_sent_job_does_not_trigger_second_control_model():\n    from app.services.semantic_image_intent_router import (\n        SemanticImageDecision, SemanticImageAction, SemanticImageRouterContext,\n        RecentImageJobSummary, resolve_active_image_job_followup_semantically,\n    )\n    class Client:\n        async def complete_result(self, *args, **kwargs):\n            raise AssertionError("control model must not run for a stale image")\n    model=SimpleNamespace(client=Client(), model="test", timeout_seconds=1)\n    context=SemanticImageRouterContext(\n        current_user_message="امروز چه خبر",\n        latest_image_job=RecentImageJobSummary(job_id=99,status="sent",action="generate_new"),\n        seconds_since_recent_image=3600,\n    )\n    initial=SemanticImageDecision(action=SemanticImageAction.CHAT,media_delivery_requested=False,confidence=.9,reason_code="chat")\n    resolved=asyncio.run(resolve_active_image_job_followup_semantically(context, initial, model=model))\n    assert resolved is initial\n'''
test_path.write_text(test)
print('patch_active_job_followup_freshness: ok')
