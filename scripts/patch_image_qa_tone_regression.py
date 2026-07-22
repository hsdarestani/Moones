from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label}: target not found")
    return text.replace(old, new, 1)


# 1) Never splice internal infrastructure text into a conversational sentence.
path = Path("app/services/interaction_reliability.py")
text = path.read_text(encoding="utf-8")
old = '''def block_unbacked_image_promise(text: str, *, image_action_succeeded: bool = False) -> tuple[str, bool]:
    if image_action_succeeded or not _IMAGE_PROMISE.search(text or ""):
        return text, False
    logger.info("IMAGE_PROMISE_BLOCKED reason=normal_chat_without_artifact_or_job")
    return _IMAGE_PROMISE.sub("برای فرستادن عکس باید درخواست عکس با موفقیت ثبت بشه", text), True
'''
new = '''def block_unbacked_image_promise(text: str, *, image_action_succeeded: bool = False) -> tuple[str, bool]:
    if image_action_succeeded or not _IMAGE_PROMISE.search(text or ""):
        return text, False
    logger.info("IMAGE_PROMISE_BLOCKED reason=normal_chat_without_artifact_or_job")
    # Never splice an operational explanation into the middle of partner dialogue.
    # The caller must regenerate the whole message or use a whole-message fallback.
    return "", True
'''
text = replace_once(text, old, new, "whole-message image promise guard")
path.write_text(text, encoding="utf-8")


# 2) Regenerate normal chat naturally when it promised media without a registered image job.
path = Path("app/engine/simple_chat.py")
text = path.read_text(encoding="utf-8")
old = '''    final = final.replace("عکس می‌سازم", "یه عکس می‌گیرم برات").replace("عکس درست می‌کنم", "یه عکس می‌فرستم").replace("تصویر تولید می‌کنم", "یه عکس می‌فرستم")
    final, promise_blocked = block_unbacked_image_promise(final)
    if promise_blocked:
        retry_used = True
'''
new = '''    final = final.replace("عکس می‌سازم", "یه عکس می‌گیرم برات").replace("عکس درست می‌کنم", "یه عکس می‌فرستم").replace("تصویر تولید می‌کنم", "یه عکس می‌فرستم")
    final, promise_blocked = block_unbacked_image_promise(final)
    if promise_blocked:
        retry_used = True
        media_retry_prompt = prompt + """
[Media-grounding correction]
Your previous answer promised to take or send a photo, but this turn is ordinary text chat and no image job was registered.
Rewrite the entire answer as one natural casual Persian Telegram message that directly answers the user's actual latest question.
Do not mention registration, requests, jobs, pipelines, systems, capabilities, tools, policies, or internal limitations.
Do not promise a photo or say you are about to send one.
Do not explain this correction. Return only the final partner message.
"""
        media_retry_result = None
        try:
            media_retry_result = await client.complete_result([{"role": "system", "content": media_retry_prompt}], model=model, parameters=parameters)
        except Exception as exc:
            logger.warning("IMAGE_PROMISE_NATURAL_RETRY_FAILED user_id=%s error_type=%s", user.id, type(exc).__name__)
        media_retry_text = raw_llm_final_text(_clean_assistant_text(media_retry_result.text, profile["partner_name"])) if media_retry_result and media_retry_result.text else ""
        media_retry_text, retry_still_promises = block_unbacked_image_promise(media_retry_text)
        if media_retry_text and not retry_still_promises:
            final = media_retry_text
            result = media_retry_result
            logger.info("IMAGE_PROMISE_NATURAL_RETRY_USED user_id=%s", user.id)
        else:
            final = "همون خودمم؛ فقط نور و زاویه ممکنه یه کم فرق کنه 😄"
            deterministic_repair_used = True
            logger.info("IMAGE_PROMISE_NATURAL_FALLBACK_USED user_id=%s", user.id)
'''
text = replace_once(text, old, new, "natural whole-message media retry")
old = '''        or deterministic_repair_used
        or (style_plan.tone == "plain" and style_plan.emotional_intensity <= 0.2)
'''
new = '''        or deterministic_repair_used
        or promise_blocked
        or (style_plan.tone == "plain" and style_plan.emotional_intensity <= 0.2)
'''
text = replace_once(text, old, new, "disable extras after media repair")
old = '''        "deterministic_repair_used": deterministic_repair_used,
        "disable_human_extras": disable_human_extras,
'''
new = '''        "deterministic_repair_used": deterministic_repair_used,
        "unbacked_image_promise_blocked": promise_blocked,
        "disable_human_extras": disable_human_extras,
'''
text = replace_once(text, old, new, "record media repair metadata")
path.write_text(text, encoding="utf-8")


# 3) Add a compact retry path for vision QA so one large-schema/provider failure is not final.
path = Path("app/services/generated_image_qa_service.py")
text = path.read_text(encoding="utf-8")
anchor = '''def _bool(v):
'''
helpers = '''COMPACT_QA_PROMPT='''You are a compact fail-closed visual QA reviewer. Return one JSON object only; no prose and no real-person identification. Verify subject count, scene, framing, identity continuity, camera method and physical capture plausibility. For a casual selfie, the viewpoint must originate from the held phone lens at arm length, the phone must be outside a non-mirror frame, and there must be no external or overhead third-person camera. Schema: {"person_count":1,"face_count":1,"intended_subject_count":1,"unexpected_additional_person_visible":false,"background_extra_person_visible":false,"duplicate_subject_visible":false,"reflection_visible":false,"reflection_matches_primary_subject":true,"reflected_distinct_person_visible":false,"selfie_detected":true,"mirror_selfie_detected":false,"confidence":"high","framing":"medium","framing_matches_request":true,"head_inside_frame":true,"feet_inside_frame":true,"body_not_cropped":true,"requested_scene_visible":true,"requested_support_surface_visible":true,"requested_pose_matches":true,"requested_clothing_visible":true,"no_clothing_regression":true,"no_unwanted_nudity":true,"identity_consistency_reasonable":true,"primary_subject_matches_request":true,"pet_visible":false,"required_objects_visible":true,"partner_visible":true,"face_visible":true,"face_hidden_matches_request":true,"back_to_camera_matches_request":true,"camera_mode_matches_request":true,"camera_source_geometry_consistent":true,"selfie_lens_perspective_plausible":true,"third_person_viewpoint_detected":false,"visible_held_phone_detected":false,"natural_capture_plausible":true,"looks_like_id_photo":false,"hands_only_matches_request":true,"looking_toward_camera":true,"eye_contact_matches_request":true,"reason_codes":[]}'''


def _compact_qa_prompt_with_requirements(visual_requirements: dict | None, *, expected_subject_count: int, expected_interaction: str | None) -> str:
    vr=visual_requirements or {}
    contract=vr.get('photo_contract') or {}
    payload={
        'expected_subject_count': expected_subject_count,
        'expected_interaction': expected_interaction,
        'framing_requirement': vr.get('framing_requirement'),
        'environment_visibility_required': bool(vr.get('environment_visibility_required') or (vr.get('visibility_targets') or {}).get('environment_visible')),
        'required_scene_elements': (vr.get('must_satisfy') or {}).get('required_scene_elements') or [],
        'required_support_surface_elements': (vr.get('must_satisfy') or {}).get('required_support_surface_elements') or [],
        'required_pose_elements': (vr.get('must_satisfy') or {}).get('required_pose_elements') or [],
        'required_visible_objects': vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects') or [],
        'photo_contract': contract,
        'eye_contact_required': bool(vr.get('eye_contact_required')),
    }
    return COMPACT_QA_PROMPT + "\nRequirements: " + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _qa_payload_missing_required_fields(payload: dict | None, visual_requirements: dict | None) -> list[str]:
    if not isinstance(payload, dict):
        return ['payload']
    required=['person_count','face_count','confidence','framing','framing_matches_request']
    vr=visual_requirements or {}
    contract=vr.get('photo_contract') or {}
    if vr.get('environment_visibility_required') or (vr.get('visibility_targets') or {}).get('environment_visible') or contract.get('current_scene_from_chat'):
        required.append('requested_scene_visible')
    if contract.get('identity_consistency_required') and contract.get('identity_visibility_scope') != 'absent':
        required.append('identity_consistency_reasonable')
    if contract.get('natural_capture_required', True):
        required.extend(['natural_capture_plausible','looks_like_id_photo'])
    if contract.get('camera_mode'):
        required.append('camera_mode_matches_request')
    if contract.get('camera_mode') == 'casual_selfie':
        required.extend(['selfie_detected','camera_source_geometry_consistent','selfie_lens_perspective_plausible','third_person_viewpoint_detected','visible_held_phone_detected'])
    elif contract.get('camera_mode') == 'mirror_selfie':
        required.extend(['mirror_selfie_detected','camera_source_geometry_consistent','third_person_viewpoint_detected'])
    if contract.get('face_visible') is True:
        required.append('face_visible')
    if contract.get('face_hidden'):
        required.append('face_hidden_matches_request')
    if contract.get('back_to_camera'):
        required.append('back_to_camera_matches_request')
    if contract.get('hands_only'):
        required.append('hands_only_matches_request')
    missing=[key for key in dict.fromkeys(required) if key not in payload or payload.get(key) is None]
    if not isinstance(payload.get('person_count'), int) or isinstance(payload.get('person_count'), bool):
        missing.append('person_count_invalid')
    if str(payload.get('confidence') or '').lower() not in {'low','medium','high'}:
        missing.append('confidence_invalid')
    return list(dict.fromkeys(missing))


'''
if helpers not in text:
    if anchor not in text:
        raise RuntimeError("QA helper anchor not found")
    text = text.replace(anchor, helpers + anchor, 1)
old = '''async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
    models=[settings.vision_model]
    if settings.vision_fallback_model and settings.vision_fallback_model not in models: models.append(settings.vision_fallback_model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    for model in models:
        logger.info('IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s', model, checksum)
        try:
            payload=await analyze_image_bytes_with_venice(image_bytes, prompt=_qa_prompt_with_requirements(visual_requirements), model=model)
            result=evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model, visual_requirements=visual_requirements, previous_metadata=previous_metadata)
            logger.info('IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', result.model, result.person_count, result.face_count, result.confidence, result.reason_codes, checksum)
            return result
        except Exception:
            logger.warning('IMAGE_GENERATED_QA_COMPLETED qa_model=%s confidence=failed reason_codes=%s artifact_checksum_prefix=%s', model, ['qa_provider_failure'], checksum)
    return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
'''
new = '''async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
    models=[settings.vision_model]
    if settings.vision_fallback_model and settings.vision_fallback_model not in models: models.append(settings.vision_fallback_model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    parsed_result=None
    for model in models:
        logger.info('IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s phase=primary', model, checksum)
        payload=None
        try:
            payload=await analyze_image_bytes_with_venice(image_bytes, prompt=_qa_prompt_with_requirements(visual_requirements), model=model)
        except Exception as exc:
            logger.warning('IMAGE_GENERATED_QA_ATTEMPT_FAILED qa_model=%s phase=primary error_type=%s artifact_checksum_prefix=%s', model, type(exc).__name__, checksum)
        missing=_qa_payload_missing_required_fields(payload, visual_requirements)
        if missing:
            logger.info('IMAGE_GENERATED_QA_COMPACT_RETRY qa_model=%s missing_fields=%s artifact_checksum_prefix=%s', model, missing, checksum)
            compact_payload=None
            for compact_attempt in range(2):
                try:
                    compact_payload=await analyze_image_bytes_with_venice(image_bytes, prompt=_compact_qa_prompt_with_requirements(visual_requirements, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction), model=model)
                    if not _qa_payload_missing_required_fields(compact_payload, visual_requirements):
                        break
                except Exception as exc:
                    logger.warning('IMAGE_GENERATED_QA_ATTEMPT_FAILED qa_model=%s phase=compact attempt=%s error_type=%s artifact_checksum_prefix=%s', model, compact_attempt + 1, type(exc).__name__, checksum)
                    compact_payload=None
            if compact_payload is not None and not _qa_payload_missing_required_fields(compact_payload, visual_requirements):
                payload=compact_payload
            else:
                continue
        result=evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model, visual_requirements=visual_requirements, previous_metadata=previous_metadata)
        parsed_result=result
        logger.info('IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', result.model, result.person_count, result.face_count, result.confidence, result.reason_codes, checksum)
        if 'qa_uncertain' in (result.reason_codes or []) and model != models[-1]:
            continue
        return result
    if parsed_result is not None:
        return parsed_result
    return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
'''
text = replace_once(text, old, new, "compact/fallback vision QA")
path.write_text(text, encoding="utf-8")


# 4) Treat a true QA-provider outage as transient instead of immediately refunding/failing.
path = Path("app/services/image_generation_service.py")
text = path.read_text(encoding="utf-8")
old = '''class SingleSubjectImageQualityError(Exception):
    pass
'''
new = '''class SingleSubjectImageQualityError(Exception):
    pass


class GeneratedImageQATransientError(Exception):
    pass


def generated_image_qa_failure_is_transient(reason_codes) -> bool:
    return 'qa_provider_failure' in set(reason_codes or [])
'''
text = replace_once(text, old, new, "transient QA exception")
old = '''    if not qa.passed:
        logger.info('IMAGE_QA_INTENT_FAILURE user_id=%s job_id=%s action=%s continuity_mode=%s qa_results=%s', getattr(job,'user_id',None), getattr(job,'id',None), (job.metadata_json or {}).get('route_action'), (job.metadata_json or {}).get('continuity_mode'), qa.reason_codes)
        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
'''
new = '''    if not qa.passed:
        logger.info('IMAGE_QA_INTENT_FAILURE user_id=%s job_id=%s action=%s continuity_mode=%s qa_results=%s', getattr(job,'user_id',None), getattr(job,'id',None), (job.metadata_json or {}).get('route_action'), (job.metadata_json or {}).get('continuity_mode'), qa.reason_codes)
        if generated_image_qa_failure_is_transient(qa.reason_codes):
            raise GeneratedImageQATransientError('generated-image QA provider unavailable')
        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
'''
text = replace_once(text, old, new, "candidate transient QA")
old = '''                if not qa.passed:
                    rejected_quality.append({'model':attempt_model,'reason_codes':qa.reason_codes,'person_count':qa.person_count,'face_count':qa.face_count,'confidence':qa.confidence,'artifact_checksum_prefix':response_checksum[:12]})
'''
new = '''                if not qa.passed:
                    if generated_image_qa_failure_is_transient(qa.reason_codes):
                        job.metadata_json={**(job.metadata_json or {}),'qa_provider_retry_pending':True,'last_qa_provider_failure_model':qa.model,'last_qa_provider_failure_checksum_prefix':response_checksum[:12]}
                        logger.warning('IMAGE_QA_PROVIDER_TRANSIENT job_id=%s user_id=%s chat_id=%s attempt_count=%s qa_model=%s', job.id, job.user_id, job.chat_id, job.attempt_count, qa.model)
                        raise GeneratedImageQATransientError('generated-image QA provider unavailable')
                    rejected_quality.append({'model':attempt_model,'reason_codes':qa.reason_codes,'person_count':qa.person_count,'face_count':qa.face_count,'confidence':qa.confidence,'artifact_checksum_prefix':response_checksum[:12]})
'''
text = replace_once(text, old, new, "generation loop transient QA")
old = '''                    if not replacement_qa.passed:
                        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
'''
new = '''                    if not replacement_qa.passed:
                        if generated_image_qa_failure_is_transient(replacement_qa.reason_codes):
                            raise GeneratedImageQATransientError('generated-image QA provider unavailable during duplicate retry')
                        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
'''
text = replace_once(text, old, new, "duplicate branch transient QA")
old = '''                    if not replacement_qa.passed:
                        logger.warning('IMAGE_SINGLE_SUBJECT_QA_FAILED job_id=%s user_id=%s chat_id=%s generation_model=%s qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, successful_model, replacement_qa.model, replacement_qa.person_count, replacement_qa.face_count, replacement_qa.confidence, replacement_qa.reason_codes, replacement_checksum[:12])
                        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
'''
new = '''                    if not replacement_qa.passed:
                        logger.warning('IMAGE_SINGLE_SUBJECT_QA_FAILED job_id=%s user_id=%s chat_id=%s generation_model=%s qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', job.id, job.user_id, job.chat_id, successful_model, replacement_qa.model, replacement_qa.person_count, replacement_qa.face_count, replacement_qa.confidence, replacement_qa.reason_codes, replacement_checksum[:12])
                        if generated_image_qa_failure_is_transient(replacement_qa.reason_codes):
                            raise GeneratedImageQATransientError('generated-image QA provider unavailable during variation retry')
                        raise SingleSubjectImageQualityError('single-subject generated-image QA failed')
'''
text = replace_once(text, old, new, "variation branch transient QA")
old = '''            job.error_code = 'provider_failure'
            job.error_message = str(exc)[:500]
            logger.info('IMAGE_FAILURE_CATEGORY user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s reason_codes=%s', job.user_id, job.image_action, job.source_image_job_id, (job.metadata_json or {}).get('continuity_mode'), (job.metadata_json or {}).get('continuity_seed_strategy'), job.prompt_engine_version, ['provider_transport_failure'])
            if job.status=='failed' and charge: billing.refund(db, charge=charge, error=job.error_message)
        job.failed_at=datetime.utcnow(); job.lock_expires_at=None; sync_image_request_chain_state(job, ImageRequestState.FAILED if job.status in {'failed','delivery_failed'} else ImageRequestState.QUEUED); db.flush(); return job
'''
new = '''            qa_transient=isinstance(exc, GeneratedImageQATransientError)
            job.error_code = 'image_qa_transient' if qa_transient else 'provider_failure'
            job.error_message = str(exc)[:500]
            reason_codes=['qa_provider_retry_exhausted' if job.status == 'failed' else 'qa_provider_retry_scheduled'] if qa_transient else ['provider_transport_failure']
            logger.info('IMAGE_FAILURE_CATEGORY user_id=%s action=%s source_job_id=%s continuity_mode=%s seed_strategy=%s prompt_engine_version=%s reason_codes=%s', job.user_id, job.image_action, job.source_image_job_id, (job.metadata_json or {}).get('continuity_mode'), (job.metadata_json or {}).get('continuity_seed_strategy'), job.prompt_engine_version, reason_codes)
            if qa_transient and job.status == 'queued':
                job.scheduled_at=datetime.utcnow()+timedelta(seconds=min(30, max(5, job.attempt_count * 5)))
                job.metadata_json={**(job.metadata_json or {}),'qa_provider_retry_pending':True,'qa_provider_retry_attempt':job.attempt_count}
            if job.status=='failed':
                if qa_transient and telegram_service and hasattr(telegram_service, 'send_text'):
                    await telegram_service.send_text(job.chat_id, 'بررسی عکس چند بار قطع شد؛ عکس ارسال نشد و سکه‌ات برگشت.')
                if charge: billing.refund(db, charge=charge, error=job.error_message)
        job.failed_at=datetime.utcnow() if job.status in {'failed','delivery_failed'} else None; job.lock_expires_at=None; sync_image_request_chain_state(job, ImageRequestState.FAILED if job.status in {'failed','delivery_failed'} else ImageRequestState.QUEUED); db.flush(); return job
'''
text = replace_once(text, old, new, "retry transient QA instead of immediate failure")
path.write_text(text, encoding="utf-8")


# 5) Regression tests for both failures shown by the user.
path = Path("tests/test_interaction_reliability.py")
text = path.read_text(encoding="utf-8")
old = '''    fixed, blocked = block_unbacked_image_promise("باشه، الان عکس می‌فرستم")
    assert blocked and "الان عکس می‌فرستم" not in fixed
'''
new = '''    fixed, blocked = block_unbacked_image_promise("باشه، الان عکس می‌فرستم")
    assert blocked and fixed == ""
    assert "درخواست عکس" not in fixed and "موفقیت ثبت" not in fixed
'''
text = replace_once(text, old, new, "no internal text in promise guard test")
old = '''from app.services.interaction_reliability import (
    aggregate_voice_feedback, block_unbacked_image_promise, interpret_sticker,
    resolve_response_style,
)
'''
new = '''from app.services.interaction_reliability import (
    aggregate_voice_feedback, block_unbacked_image_promise, interpret_sticker,
    resolve_response_style,
)
from app.services.image_generation_service import generated_image_qa_failure_is_transient
'''
text = replace_once(text, old, new, "import transient QA helper")
text += '''\n\ndef test_qa_provider_failure_is_retryable_but_real_visual_failure_is_not():\n    assert generated_image_qa_failure_is_transient(["qa_provider_failure", "qa_uncertain"])\n    assert not generated_image_qa_failure_is_transient(["wrong_scene", "selfie_geometry_inconsistent"])\n'''
path.write_text(text, encoding="utf-8")

path = Path("tests/test_partner_selfie_continuity.py")
text = path.read_text(encoding="utf-8")
if "import asyncio\n" not in text:
    text = "import asyncio\nfrom types import SimpleNamespace\n\nimport app.services.generated_image_qa_service as qa_service\n" + text
text += '''\n\ndef test_legacy_qa_payload_gets_compact_retry_for_new_selfie_geometry(monkeypatch):\n    legacy = _qa_payload()\n    for key in (\n        "camera_source_geometry_consistent",\n        "selfie_lens_perspective_plausible",\n        "third_person_viewpoint_detected",\n        "visible_held_phone_detected",\n    ):\n        legacy.pop(key, None)\n    complete = _qa_payload()\n    calls = []\n\n    async def fake_analyze(image_bytes, *, prompt, model):\n        calls.append(prompt)\n        return complete if prompt.startswith("You are a compact fail-closed") else legacy\n\n    monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", fake_analyze)\n    monkeypatch.setattr(\n        qa_service,\n        "get_settings",\n        lambda: SimpleNamespace(venice_api_key="test", vision_model="vision-primary", vision_fallback_model=""),\n    )\n    result = asyncio.run(qa_service.evaluate_generated_image_composition(\n        b"image-bytes",\n        expected_subject_count=1,\n        selfie_allowed=True,\n        visual_requirements=_requirements(),\n    ))\n    assert result.passed is True\n    assert len(calls) == 2\n    assert calls[1].startswith("You are a compact fail-closed")\n\n\ndef test_compact_qa_retries_once_after_transient_provider_error(monkeypatch):\n    complete = _qa_payload()\n    call_count = {"value": 0}\n\n    async def fake_analyze(image_bytes, *, prompt, model):\n        call_count["value"] += 1\n        if call_count["value"] < 3:\n            raise RuntimeError("temporary vision failure")\n        return complete\n\n    monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", fake_analyze)\n    monkeypatch.setattr(\n        qa_service,\n        "get_settings",\n        lambda: SimpleNamespace(venice_api_key="test", vision_model="vision-primary", vision_fallback_model=""),\n    )\n    result = asyncio.run(qa_service.evaluate_generated_image_composition(\n        b"image-bytes",\n        expected_subject_count=1,\n        selfie_allowed=True,\n        visual_requirements=_requirements(),\n    ))\n    assert result.passed is True\n    assert call_count["value"] == 3\n'''
path.write_text(text, encoding="utf-8")
