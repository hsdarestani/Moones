from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:220]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"start marker not found in {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"end marker not found in {path}: {end!r}")
    file_path.write_text(
        text[:start_index] + replacement + "\n\n" + text[end_index:],
        encoding="utf-8",
    )


replace_once(
    "app/core/config.py",
    '''    vision_model: str = "qwen3-vl-235b-a22b"\n    vision_fallback_model: str = "e2ee-qwen3-vl-30b-a3b-p"\n''',
    '''    vision_model: str = "qwen3-vl-235b-a22b"\n    vision_fallback_model: str = "e2ee-qwen3-vl-30b-a3b-p"\n    vision_request_timeout_seconds: int = 45\n    image_generation_qa_timeout_seconds: int = 50\n    image_generation_qa_attempts_per_model: int = 2\n    image_generation_anatomy_qa_timeout_seconds: int = 50\n    image_generation_anatomy_qa_attempts_per_model: int = 2\n''',
)

helpers = '''def _bounded_qa_int(settings, name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _bounded_qa_timeout(settings, name: str, default: float) -> float:
    try:
        value = float(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return min(120.0, max(15.0, value))


def _provider_transient_anatomy_result(result: GeneratedImageQAResult) -> bool:
    return bool(
        set(result.reason_codes or [])
        & {"anatomy_qa_provider_failure", "qa_uncertain"}
    )


'''
replace_once(
    "app/services/generated_image_qa_service.py",
    "async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:\n",
    helpers + "async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:\n",
)

composition_function = '''async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)

    models=[]
    for candidate in (settings.vision_model, settings.vision_fallback_model):
        model=str(candidate or '').strip()
        if model and model not in models:
            models.append(model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    if not models:
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)

    primary_model=models[0]
    fallback_model=models[1] if len(models) > 1 else None
    attempts=[
        (primary_model, _qa_prompt_with_requirements(visual_requirements), 'primary'),
    ]
    if fallback_model:
        attempts.append((fallback_model, _compact_qa_prompt_with_requirements(visual_requirements, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction), 'compact_fallback'))

    attempts_per_model=_bounded_qa_int(
        settings,
        'image_generation_qa_attempts_per_model',
        2,
        minimum=1,
        maximum=3,
    )
    timeout_seconds=_bounded_qa_timeout(
        settings,
        'image_generation_qa_timeout_seconds',
        50.0,
    )
    parsed_result=None

    for model, prompt, phase in attempts:
        for reviewer_attempt in range(1, attempts_per_model + 1):
            logger.info(
                'IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s phase=%s reviewer_attempt=%s max_attempts=%s',
                model,
                checksum,
                phase,
                reviewer_attempt,
                attempts_per_model,
            )
            try:
                payload=await asyncio.wait_for(
                    analyze_image_bytes_with_venice(image_bytes, prompt=prompt, model=model),
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                will_retry=reviewer_attempt < attempts_per_model
                logger.warning(
                    'IMAGE_GENERATED_QA_ATTEMPT_FAILED qa_model=%s phase=%s reviewer_attempt=%s max_attempts=%s will_retry=%s error_type=%s artifact_checksum_prefix=%s',
                    model,
                    phase,
                    reviewer_attempt,
                    attempts_per_model,
                    will_retry,
                    type(exc).__name__,
                    checksum,
                )
                if will_retry:
                    continue
                break

            missing=_qa_payload_missing_required_fields(payload, visual_requirements)
            if missing:
                will_retry=reviewer_attempt < attempts_per_model
                logger.info(
                    'IMAGE_GENERATED_QA_PAYLOAD_INCOMPLETE qa_model=%s phase=%s reviewer_attempt=%s max_attempts=%s will_retry=%s missing_fields=%s artifact_checksum_prefix=%s',
                    model,
                    phase,
                    reviewer_attempt,
                    attempts_per_model,
                    will_retry,
                    missing,
                    checksum,
                )
                if will_retry:
                    continue
                break

            result=evaluate_generated_image_composition_payload(
                payload,
                expected_subject_count=expected_subject_count,
                expected_interaction=expected_interaction,
                selfie_allowed=selfie_allowed,
                mirror_allowed=mirror_allowed,
                model=model,
                visual_requirements=visual_requirements,
                previous_metadata=previous_metadata,
            )
            parsed_result=result
            logger.info(
                'IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s reviewer_attempt=%s',
                result.model,
                result.person_count,
                result.face_count,
                result.confidence,
                result.reason_codes,
                checksum,
                reviewer_attempt,
            )
            if 'qa_uncertain' in (result.reason_codes or []):
                if reviewer_attempt < attempts_per_model:
                    logger.info(
                        'IMAGE_GENERATED_QA_TRANSIENT_RETRY qa_model=%s phase=%s reviewer_attempt=%s artifact_checksum_prefix=%s',
                        model,
                        phase,
                        reviewer_attempt,
                        checksum,
                    )
                    continue
                if phase == 'primary' and fallback_model:
                    break
            return result

    if parsed_result is not None:
        return parsed_result
    return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
'''
replace_between(
    "app/services/generated_image_qa_service.py",
    "async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int",
    "async def evaluate_single_subject_image",
    composition_function,
)

anatomy_function = '''async def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        logger.warning('ADULT_ANATOMY_QA_FAILED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, 'low', ['anatomy_qa_provider_failure'])
        return merge_adult_anatomy_qa_results([])

    reviewer_candidates=[
        (settings.vision_model, ADULT_ANATOMY_PROFILE_QA_PROMPT, 'profile'),
        (getattr(settings, 'vision_fallback_model', None), ADULT_ANATOMY_STRUCTURE_QA_PROMPT, 'structure'),
    ]
    review_plan=[]
    seen_models=set()
    for model, review_prompt, phase in reviewer_candidates:
        normalized=str(model or '').strip()
        if not normalized or normalized in seen_models:
            continue
        seen_models.add(normalized)
        review_plan.append((normalized, review_prompt, phase))

    attempts_per_model=_bounded_qa_int(
        settings,
        'image_generation_anatomy_qa_attempts_per_model',
        2,
        minimum=1,
        maximum=3,
    )
    timeout_seconds=_bounded_qa_timeout(
        settings,
        'image_generation_anatomy_qa_timeout_seconds',
        50.0,
    )
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    logger.info('ADULT_ANATOMY_QA_STARTED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s reviewer_count=%s attempts_per_model=%s artifact_checksum_prefix=%s', user_id, job_id, request_chain_id, anatomical_profile, None, [], len(review_plan), attempts_per_model, checksum)
    results=[]

    for model, review_prompt, phase in review_plan:
        prompt=review_prompt + "\nSchema: " + ADULT_ANATOMY_QA_SCHEMA + "\nRequirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)
        reviewer_result=None
        for reviewer_attempt in range(1, attempts_per_model + 1):
            try:
                payload=await asyncio.wait_for(
                    analyze_image_bytes_with_venice(image_bytes, prompt=prompt, model=model),
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                will_retry=reviewer_attempt < attempts_per_model
                logger.warning(
                    'ADULT_ANATOMY_QA_ATTEMPT_FAILED user_id=%s job_id=%s request_chain_id=%s qa_model=%s phase=%s reviewer_attempt=%s max_attempts=%s will_retry=%s error_type=%s artifact_checksum_prefix=%s',
                    user_id,
                    job_id,
                    request_chain_id,
                    model,
                    phase,
                    reviewer_attempt,
                    attempts_per_model,
                    will_retry,
                    type(exc).__name__,
                    checksum,
                )
                if will_retry:
                    continue
                reviewer_result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],model)
                break

            candidate=evaluate_adult_anatomy_payload(
                payload,
                anatomical_profile=anatomical_profile,
                model=model,
            )
            transient=_provider_transient_anatomy_result(candidate)
            will_retry=transient and reviewer_attempt < attempts_per_model
            logger.info(
                'ADULT_ANATOMY_QA_REVIEW_COMPLETED user_id=%s job_id=%s request_chain_id=%s qa_model=%s phase=%s reviewer_attempt=%s confidence=%s reason_codes=%s transient=%s will_retry=%s artifact_checksum_prefix=%s',
                user_id,
                job_id,
                request_chain_id,
                model,
                phase,
                reviewer_attempt,
                candidate.confidence,
                candidate.reason_codes,
                transient,
                will_retry,
                checksum,
            )
            if will_retry:
                continue
            reviewer_result=candidate
            break

        if reviewer_result is None:
            reviewer_result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],model)
        results.append(reviewer_result)

    result=merge_adult_anatomy_qa_results(results)
    logger.info('ADULT_ANATOMY_QA_COMPLETED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s reviewer_count=%s artifact_checksum_prefix=%s', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes, len(results), checksum)
    logger.info('ADULT_ANATOMY_QA_%s user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', 'PASSED' if result.passed else 'FAILED', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    return result
'''
replace_between(
    "app/services/generated_image_qa_service.py",
    "async def evaluate_adult_anatomy_image",
    "def metadata_has_valid_generated_image_qa",
    anatomy_function,
)
