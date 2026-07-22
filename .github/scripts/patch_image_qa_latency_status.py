from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


# 1) Bound generated-image QA to at most two Vision calls.
qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text()
if "import asyncio" not in qa.splitlines()[:10]:
    qa = replace_once(qa, "from __future__ import annotations\n", "from __future__ import annotations\nimport asyncio\n", "qa asyncio import")

new_qa_function = '''async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
    models=[settings.vision_model]
    if settings.vision_fallback_model and settings.vision_fallback_model not in models:
        models.append(settings.vision_fallback_model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    primary_model=models[0]
    fallback_model=models[1] if len(models) > 1 else primary_model
    attempts=[
        (primary_model, _qa_prompt_with_requirements(visual_requirements), 'primary'),
        (fallback_model, _compact_qa_prompt_with_requirements(visual_requirements, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction), 'compact_fallback'),
    ]
    parsed_result=None
    for model, prompt, phase in attempts:
        logger.info('IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s phase=%s', model, checksum, phase)
        try:
            payload=await asyncio.wait_for(
                analyze_image_bytes_with_venice(image_bytes, prompt=prompt, model=model),
                timeout=10,
            )
        except Exception as exc:
            logger.warning('IMAGE_GENERATED_QA_ATTEMPT_FAILED qa_model=%s phase=%s error_type=%s artifact_checksum_prefix=%s', model, phase, type(exc).__name__, checksum)
            continue
        missing=_qa_payload_missing_required_fields(payload, visual_requirements)
        if missing:
            logger.info('IMAGE_GENERATED_QA_PAYLOAD_INCOMPLETE qa_model=%s phase=%s missing_fields=%s artifact_checksum_prefix=%s', model, phase, missing, checksum)
            continue
        result=evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model, visual_requirements=visual_requirements, previous_metadata=previous_metadata)
        parsed_result=result
        logger.info('IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', result.model, result.person_count, result.face_count, result.confidence, result.reason_codes, checksum)
        if 'qa_uncertain' in (result.reason_codes or []) and phase == 'primary':
            continue
        return result
    if parsed_result is not None:
        return parsed_result
    return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
'''
qa = regex_once(
    qa,
    r"async def evaluate_generated_image_composition\(.*?\n(?=async def evaluate_single_subject_image)",
    new_qa_function + "\n",
    "bounded QA function",
)
qa_path.write_text(qa)

# 2) Normal images degrade gracefully on a pure QA-provider outage; adult QA stays fail-closed.
svc_path = Path("app/services/image_generation_service.py")
svc = svc_path.read_text()
helper_anchor = "def generated_image_qa_failure_is_transient(reason_codes) -> bool:\n    return 'qa_provider_failure' in set(reason_codes or [])\n"
helpers = helper_anchor + '''\n\ndef generated_image_qa_can_degrade(job, qa) -> bool:\n    if not generated_image_qa_failure_is_transient(getattr(qa, 'reason_codes', None)):\n        return False\n    metadata=getattr(job, 'metadata_json', None) or {}\n    requirements=metadata.get('visual_requirements') or {}\n    return not bool(requirements.get('anatomy_qa_required') or requirements.get('explicit_nudity_requested'))\n\n\ndef accept_degraded_generated_image_qa(qa):\n    original=list(getattr(qa, 'reason_codes', None) or [])\n    qa.passed=True\n    qa.reason_codes=[]\n    setattr(qa, 'raw_provider_reason_codes', original)\n    setattr(qa, 'qa_degraded_provider_unavailable', True)\n    return qa\n\n\nasync def _safe_send_image_status(telegram_service, chat_id: int, text: str) -> None:\n    if not telegram_service or not hasattr(telegram_service, 'send_text'):\n        return\n    try:\n        await telegram_service.send_text(chat_id, text)\n    except Exception:\n        logger.exception('IMAGE_FAILURE_NOTICE_SEND_FAILED chat_id=%s', chat_id)\n'''
svc = replace_once(svc, helper_anchor, helpers, "QA degradation helpers")
svc = replace_once(
    svc,
    """    if not qa.passed:\n        logger.info('IMAGE_QA_INTENT_FAILURE user_id=%s job_id=%s action=%s continuity_mode=%s qa_results=%s', getattr(job,'user_id',None), getattr(job,'id',None), (job.metadata_json or {}).get('route_action'), (job.metadata_json or {}).get('continuity_mode'), qa.reason_codes)\n        if generated_image_qa_failure_is_transient(qa.reason_codes):\n            raise GeneratedImageQATransientError('generated-image QA provider unavailable')\n        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')\n""",
    """    if not qa.passed:\n        logger.info('IMAGE_QA_INTENT_FAILURE user_id=%s job_id=%s action=%s continuity_mode=%s qa_results=%s', getattr(job,'user_id',None), getattr(job,'id',None), (job.metadata_json or {}).get('route_action'), (job.metadata_json or {}).get('continuity_mode'), qa.reason_codes)\n        if generated_image_qa_can_degrade(job, qa):\n            qa=accept_degraded_generated_image_qa(qa)\n            logger.warning('IMAGE_QA_DEGRADED_DELIVERY_ALLOWED user_id=%s job_id=%s reason=provider_unavailable', getattr(job,'user_id',None), getattr(job,'id',None))\n        elif generated_image_qa_failure_is_transient(qa.reason_codes):\n            raise GeneratedImageQATransientError('generated-image QA provider unavailable')\n        else:\n            raise SingleSubjectImageQualityError('single-subject generated-image QA failed')\n""",
    "candidate QA degradation",
)
svc = replace_once(
    svc,
    """                if not qa.passed:\n                    if generated_image_qa_failure_is_transient(qa.reason_codes):\n                        job.metadata_json={**(job.metadata_json or {}),'qa_provider_retry_pending':True,'last_qa_provider_failure_model':qa.model,'last_qa_provider_failure_checksum_prefix':response_checksum[:12]}\n                        logger.warning('IMAGE_QA_PROVIDER_TRANSIENT job_id=%s user_id=%s chat_id=%s attempt_count=%s qa_model=%s', job.id, job.user_id, job.chat_id, job.attempt_count, qa.model)\n                        raise GeneratedImageQATransientError('generated-image QA provider unavailable')\n                    rejected_quality.append({'model':attempt_model,'reason_codes':qa.reason_codes,'person_count':qa.person_count,'face_count':qa.face_count,'confidence':qa.confidence,'artifact_checksum_prefix':response_checksum[:12]})\n""",
    """                if not qa.passed and generated_image_qa_can_degrade(job, qa):\n                    qa=accept_degraded_generated_image_qa(qa)\n                    update['qa_degraded_provider_unavailable']=True\n                    update['qa_degraded_original_reason_codes']=getattr(qa, 'raw_provider_reason_codes', [])\n                    logger.warning('IMAGE_QA_DEGRADED_DELIVERY_ALLOWED job_id=%s user_id=%s chat_id=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, response_checksum[:12])\n                if not qa.passed:\n                    if generated_image_qa_failure_is_transient(qa.reason_codes):\n                        job.metadata_json={**(job.metadata_json or {}),'last_qa_provider_failure_model':qa.model,'last_qa_provider_failure_checksum_prefix':response_checksum[:12]}\n                        logger.warning('IMAGE_QA_PROVIDER_TRANSIENT job_id=%s user_id=%s chat_id=%s attempt_count=%s qa_model=%s', job.id, job.user_id, job.chat_id, job.attempt_count, qa.model)\n                        raise GeneratedImageQATransientError('generated-image QA provider unavailable')\n                    rejected_quality.append({'model':attempt_model,'reason_codes':qa.reason_codes,'person_count':qa.person_count,'face_count':qa.face_count,'confidence':qa.confidence,'artifact_checksum_prefix':response_checksum[:12]})\n""",
    "main QA degradation",
)
svc = replace_once(
    svc,
    """            job.status = (\n                'failed'\n                if (\n                    non_retryable\n                    or job.attempt_count\n                    >= job.max_attempts\n                )\n                else 'queued'\n            )\n\n            qa_transient=isinstance(exc, GeneratedImageQATransientError)\n""",
    """            qa_transient=isinstance(exc, GeneratedImageQATransientError)\n            job.status = (\n                'failed'\n                if (\n                    qa_transient\n                    or non_retryable\n                    or job.attempt_count\n                    >= job.max_attempts\n                )\n                else 'queued'\n            )\n\n""",
    "no image regeneration for QA outage",
)
svc = replace_once(
    svc,
    """            if qa_transient and job.status == 'queued':\n                job.scheduled_at=datetime.utcnow()+timedelta(seconds=min(30, max(5, job.attempt_count * 5)))\n                job.metadata_json={**(job.metadata_json or {}),'qa_provider_retry_pending':True,'qa_provider_retry_attempt':job.attempt_count}\n            if job.status=='failed':\n                if qa_transient and telegram_service and hasattr(telegram_service, 'send_text'):\n                    await telegram_service.send_text(job.chat_id, 'بررسی عکس چند بار قطع شد؛ عکس ارسال نشد و سکه‌ات برگشت.')\n                if charge: billing.refund(db, charge=charge, error=job.error_message)\n""",
    """            if job.status=='failed':\n                if qa_transient:\n                    await _safe_send_image_status(telegram_service, job.chat_id, 'این یکی رو نتونستم مطمئن بررسی کنم؛ نفرستادمش و سکه‌ات برگشت 🤍')\n                if charge: billing.refund(db, charge=charge, error=job.error_message)\n""",
    "safe final QA failure",
)
svc_path.write_text(svc)

# 3) Add a second semantic control pass for colloquial status/cancel follow-ups.
router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
insert_before = "\ndef semantic_shadow_log_event(context: SemanticImageRouterContext, decision: SemanticImageDecision, invariant_codes: list[str] | None = None) -> dict[str, Any]:\n"
control_helper = '''\nasync def resolve_active_image_job_followup_semantically(\n    context: SemanticImageRouterContext,\n    decision: SemanticImageDecision,\n    *,\n    model=None,\n) -> SemanticImageDecision:\n    target=context.active_image_job or context.latest_image_job\n    if target is None or decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.CLARIFY}:\n        return decision\n    if str(getattr(target, 'status', '') or '') not in {'queued','processing','generating','sending','delivery_failed','failed','sent'}:\n        return decision\n    semantic_model=model or VeniceSemanticImageIntentModel()\n    payload={\n        'current_user_message': context.current_user_message,\n        'target_job': asdict(target),\n        'recent_conversation': [asdict(turn) for turn in context.recent_conversation[-4:]],\n    }\n    system=(\n        'An image job is relevant. Classify the current colloquial Persian follow-up as exactly one of status_query, cancel_pending, or chat. '\n        'status_query includes any natural way of asking what happened, whether it is ready, where the photo is, or why it is taking long, even with typos, repeated letters, vocatives, names, jokes, or extra filler. '\n        'cancel_pending means the user wants the image stopped. chat means neither. Return JSON only: {"action":"status_query|cancel_pending|chat","confidence":0.0}. Do not use phrase matching.'\n    )\n    try:\n        result=await semantic_model.client.complete_result(\n            [\n                {'role':'system','content':system},\n                {'role':'user','content':json.dumps(payload, ensure_ascii=False, sort_keys=True)},\n            ],\n            model=semantic_model.model,\n            parameters={'temperature':0.0,'top_p':0.1,'max_tokens':80,'response_format':{'type':'json_object'}},\n            timeout=min(float(getattr(semantic_model, 'timeout_seconds', 4.0)), 4.0),\n        )\n        data=json.loads(result.text or '{}')\n        action=str(data.get('action') or 'chat')\n        confidence=float(data.get('confidence') or 0.0)\n    except Exception as exc:\n        logger.info('IMAGE_ACTIVE_JOB_FOLLOWUP_MODEL_FAILED error=%s', type(exc).__name__)\n        return decision\n    if action not in {SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING} or confidence < 0.65:\n        return decision\n    logger.info('IMAGE_ACTIVE_JOB_FOLLOWUP_RESOLVED action=%s job_id=%s status=%s', action, target.job_id, target.status)\n    return SemanticImageDecision(\n        action=action,\n        media_delivery_requested=False,\n        confidence=confidence,\n        reason_code='active_image_job_followup_semantic_control',\n        needs_clarification=False,\n        source_reference=None,\n        visual_intent=decision.visual_intent,\n        safety_relevant_signals=decision.safety_relevant_signals,\n    )\n\n\ndef should_report_active_job_instead_of_enqueuing(context: SemanticImageRouterContext, decision: SemanticImageDecision) -> bool:\n    return bool(context.active_image_job and decision.action == SemanticImageAction.GENERATE_NEW)\n'''
router = replace_once(router, insert_before, control_helper + insert_before, "semantic active job control")
router_path.write_text(router)

telegram_path = Path("app/api/telegram.py")
telegram = telegram_path.read_text()
telegram = replace_once(
    telegram,
    """    enforce_referenced_object_request, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n    validate_source_reference_deterministically)\n""",
    """    enforce_referenced_object_request, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n    resolve_active_image_job_followup_semantically, should_report_active_job_instead_of_enqueuing,\n    validate_source_reference_deterministically)\n""",
    "telegram control imports",
)
telegram = replace_once(
    telegram,
    """        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)\n        logger.info(\"IMAGE_ROUTE_LLM_DECISION user_id=%s action=%s reason_code=%s source_job_id=%s\", user.id, semantic_decision.action, semantic_decision.reason_code, getattr(getattr(semantic_decision, 'source_reference', None), 'job_id', None))\n""",
    """        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)\n        semantic_decision = await resolve_active_image_job_followup_semantically(context, semantic_decision)\n        logger.info(\"IMAGE_ROUTE_LLM_DECISION user_id=%s action=%s reason_code=%s source_job_id=%s\", user.id, semantic_decision.action, semantic_decision.reason_code, getattr(getattr(semantic_decision, 'source_reference', None), 'job_id', None))\n""",
    "telegram semantic status pass",
)
telegram = replace_once(
    telegram,
    """        if pending_resolution and pending_resolution.effective_request_text is None and pending_resolution.action != SemanticImageAction.CHAT:\n""",
    """        if should_report_active_job_instead_of_enqueuing(context, semantic_decision):\n          text_status=_image_status_text(context.active_image_job)\n          if text_status:\n            logger.info('IMAGE_ACTIVE_JOB_ABSORBED_NEW_REQUEST user_id=%s job_id=%s job_status=%s', user.id, context.active_image_job.job_id, context.active_image_job.status)\n            db.commit(); await _send_user_text(svc, chat_id, text_status, user_id=user.id, surface='chat', user_text=text); return {'ok': True}\n        if pending_resolution and pending_resolution.effective_request_text is None and pending_resolution.action != SemanticImageAction.CHAT:\n""",
    "active job duplicate request status",
)
telegram_path.write_text(telegram)

# 4) Regression tests.
test_path = Path("tests/test_image_qa_latency_status.py")
test_path.write_text('''import asyncio\nfrom types import SimpleNamespace\n\n\ndef test_generated_qa_uses_at_most_two_vision_calls(monkeypatch):\n    import app.services.generated_image_qa_service as qa\n    calls=[]\n    async def fail(*args, **kwargs):\n        calls.append(kwargs.get("model"))\n        raise RuntimeError("vision down")\n    monkeypatch.setattr(qa, "analyze_image_bytes_with_venice", fail)\n    monkeypatch.setattr(qa, "get_settings", lambda: SimpleNamespace(venice_api_key="x", vision_model="primary", vision_fallback_model="fallback"))\n    result=asyncio.run(qa.evaluate_generated_image_composition(b"image", expected_subject_count=1))\n    assert result.passed is False\n    assert "qa_provider_failure" in result.reason_codes\n    assert calls == ["primary", "fallback"]\n\n\ndef test_normal_image_can_degrade_on_pure_qa_outage():\n    from app.services.generated_image_qa_service import GeneratedImageQAResult\n    from app.services.image_generation_service import generated_image_qa_can_degrade, accept_degraded_generated_image_qa\n    job=SimpleNamespace(metadata_json={"visual_requirements":{"anatomy_qa_required":False,"explicit_nudity_requested":False}})\n    result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,"low",["qa_provider_failure","qa_uncertain"],None)\n    assert generated_image_qa_can_degrade(job, result) is True\n    accepted=accept_degraded_generated_image_qa(result)\n    assert accepted.passed is True\n    assert accepted.reason_codes == []\n    assert accepted.raw_provider_reason_codes == ["qa_provider_failure","qa_uncertain"]\n\n\ndef test_adult_anatomy_qa_never_degrades():\n    from app.services.generated_image_qa_service import GeneratedImageQAResult\n    from app.services.image_generation_service import generated_image_qa_can_degrade\n    job=SimpleNamespace(metadata_json={"visual_requirements":{"anatomy_qa_required":True,"explicit_nudity_requested":True}})\n    result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,"low",["qa_provider_failure","qa_uncertain"],None)\n    assert generated_image_qa_can_degrade(job, result) is False\n\n\ndef test_colloquial_active_job_followup_gets_semantic_status():\n    from app.services.semantic_image_intent_router import (\n        SemanticImageDecision, SemanticImageAction, SemanticImageRouterContext,\n        RecentImageJobSummary, resolve_active_image_job_followup_semantically,\n    )\n    class Client:\n        async def complete_result(self, *args, **kwargs):\n            return SimpleNamespace(text='{"action":"status_query","confidence":0.96}')\n    model=SimpleNamespace(client=Client(), model="test", timeout_seconds=1)\n    context=SemanticImageRouterContext(\n        current_user_message="چیشد خبیب",\n        active_image_job=RecentImageJobSummary(job_id=12,status="processing",action="generate_new"),\n    )\n    initial=SemanticImageDecision(action=SemanticImageAction.CHAT,media_delivery_requested=False,confidence=.8,reason_code="chat")\n    resolved=asyncio.run(resolve_active_image_job_followup_semantically(context, initial, model=model))\n    assert resolved.action == SemanticImageAction.STATUS_QUERY\n\n\ndef test_new_request_is_absorbed_while_job_active():\n    from app.services.semantic_image_intent_router import (\n        SemanticImageDecision, SemanticImageAction, SemanticImageRouterContext,\n        RecentImageJobSummary, should_report_active_job_instead_of_enqueuing,\n    )\n    context=SemanticImageRouterContext(current_user_message="عکس بده ببینمت",active_image_job=RecentImageJobSummary(job_id=3,status="queued"))\n    decision=SemanticImageDecision(action=SemanticImageAction.GENERATE_NEW,media_delivery_requested=True,confidence=1,reason_code="clear")\n    assert should_report_active_job_instead_of_enqueuing(context, decision) is True\n''')

print("patch_image_qa_latency_status: ok")
