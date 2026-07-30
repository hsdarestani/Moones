from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:240]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: str, start: str, end: str, replacement: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"start marker not found in {path}: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"end marker not found in {path}: {end!r}")
    p.write_text(text[:start_index] + replacement + "\n\n" + text[end_index:], encoding="utf-8")


replace_once(
    "app/services/generated_image_qa_service.py",
    '''def _provider_transient_anatomy_result(result: GeneratedImageQAResult) -> bool:
    return bool(
        set(result.reason_codes or [])
        & {"anatomy_qa_provider_failure", "qa_uncertain"}
    )


''',
    '''def _provider_transient_anatomy_result(result: GeneratedImageQAResult) -> bool:
    return bool(
        set(result.reason_codes or [])
        & {"anatomy_qa_provider_failure", "qa_uncertain"}
    )


def _configured_vision_reviewer_models(settings, *, max_models: int | None = None) -> list[str]:
    candidates=[getattr(settings, 'vision_model', None)]
    candidates.extend(
        part.strip()
        for part in str(getattr(settings, 'vision_reviewer_models', '') or '').split(',')
        if part.strip()
    )
    candidates.append(getattr(settings, 'vision_fallback_model', None))
    models=[]
    for candidate in candidates:
        model=str(candidate or '').strip()
        if model and model not in models:
            models.append(model)
    if max_models is not None:
        models=models[:max(1, int(max_models))]
    return models


''',
)

replace_once(
    "app/services/generated_image_qa_service.py",
    '''    models=[]
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
''',
    '''    max_reviewer_models=_bounded_qa_int(
        settings,
        'image_generation_qa_max_reviewer_models',
        3,
        minimum=1,
        maximum=5,
    )
    models=_configured_vision_reviewer_models(settings, max_models=max_reviewer_models)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    if not models:
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)

    attempts=[]
    for index, model in enumerate(models):
        prompt=(
            _qa_prompt_with_requirements(visual_requirements)
            if index == 0
            else _compact_qa_prompt_with_requirements(
                visual_requirements,
                expected_subject_count=expected_subject_count,
                expected_interaction=expected_interaction,
            )
        )
        attempts.append((model, prompt, 'primary' if index == 0 else f'fallback_{index}'))
''',
)

replace_once(
    "app/services/generated_image_qa_service.py",
    "                if phase == 'primary' and fallback_model:\n                    break\n",
    "                if model != models[-1]:\n                    break\n",
)

pool_merge = '''def merge_adult_anatomy_reviewer_pool(
    results: list[GeneratedImageQAResult],
    *,
    required_reviewers: int = 2,
) -> GeneratedImageQAResult:
    required=max(2, int(required_reviewers or 2))
    unique_results=[]
    seen_models=set()
    for result in results:
        model=str(result.model or 'unknown')
        if model in seen_models:
            continue
        seen_models.add(model)
        unique_results.append(result)

    transient=[result for result in unique_results if _provider_transient_anatomy_result(result)]
    conclusive=[result for result in unique_results if result not in transient]
    substantive_failures=[result for result in conclusive if not result.passed]
    successful=[
        result for result in conclusive
        if result.passed and result.confidence in {'medium','high'}
    ]

    if substantive_failures:
        codes=list(dict.fromkeys(
            code
            for result in substantive_failures
            for code in (result.reason_codes or [])
        )) or ['anatomy_qa_disagreement']
        failure=GeneratedImageQAResult(
            False,None,None,False,False,False,False,False,False,'low',codes,
            'consensus:' + '+'.join(str(result.model or 'unknown') for result in conclusive),
            anatomy_visible_enough_to_assess=all(result.anatomy_visible_enough_to_assess is True for result in conclusive),
            anatomy_consistent_with_profile=all(result.anatomy_consistent_with_profile is True for result in conclusive),
            contradictory_sex_characteristics=any(result.contradictory_sex_characteristics is True for result in conclusive),
            malformed_anatomy=any(result.malformed_anatomy is True for result in conclusive),
            implausible_anatomy=any(result.implausible_anatomy is True for result in conclusive),
            duplicated_anatomy_parts=any(result.duplicated_anatomy_parts is True for result in conclusive),
            missing_expected_parts_when_visible=any(result.missing_expected_parts_when_visible is True for result in conclusive),
            ambiguous_anatomy=any(result.ambiguous_anatomy is True for result in conclusive),
        )
        failure.consensus_passed=False
        failure.qa_passes=[
            {'model':result.model,'passed':result.passed,'confidence':result.confidence,'reason_codes':list(result.reason_codes or [])}
            for result in conclusive
        ]
        failure.reviewer_failures=[
            {'model':result.model,'reason_codes':list(result.reason_codes or [])}
            for result in transient
        ]
        return failure

    if len(successful) >= required:
        selected=successful[:required]
        merged=merge_adult_anatomy_qa_results(selected)
        merged.qa_passes=[
            {'model':result.model,'passed':result.passed,'confidence':result.confidence,'reason_codes':list(result.reason_codes or [])}
            for result in selected
        ]
        merged.reviewer_failures=[
            {'model':result.model,'reason_codes':list(result.reason_codes or [])}
            for result in transient
        ]
        return merged

    codes=['anatomy_qa_consensus_incomplete']
    for result in transient:
        codes.extend(result.reason_codes or [])
    failure=GeneratedImageQAResult(
        False,None,None,False,False,False,False,False,False,'low',
        list(dict.fromkeys(codes)),
        'consensus:' + '+'.join(str(result.model or 'unknown') for result in successful),
    )
    failure.consensus_passed=False
    failure.qa_passes=[
        {'model':result.model,'passed':result.passed,'confidence':result.confidence,'reason_codes':list(result.reason_codes or [])}
        for result in successful
    ]
    failure.reviewer_failures=[
        {'model':result.model,'reason_codes':list(result.reason_codes or [])}
        for result in transient
    ]
    return failure
'''
replace_once(
    "app/services/generated_image_qa_service.py",
    "async def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:\n",
    pool_merge + "\n\nasync def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:\n",
)

anatomy_function = '''async def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        logger.warning('ADULT_ANATOMY_QA_FAILED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, 'low', ['anatomy_qa_provider_failure'])
        return merge_adult_anatomy_reviewer_pool([], required_reviewers=2)

    required_reviewers=_bounded_qa_int(
        settings,
        'image_generation_anatomy_required_reviewers',
        2,
        minimum=2,
        maximum=3,
    )
    max_reviewer_models=_bounded_qa_int(
        settings,
        'image_generation_anatomy_max_reviewer_models',
        3,
        minimum=required_reviewers,
        maximum=5,
    )
    reviewer_models=_configured_vision_reviewer_models(
        settings,
        max_models=max_reviewer_models,
    )
    review_plan=[]
    for index, model in enumerate(reviewer_models):
        review_plan.append((
            model,
            ADULT_ANATOMY_PROFILE_QA_PROMPT if index == 0 else ADULT_ANATOMY_STRUCTURE_QA_PROMPT,
            'profile' if index == 0 else f'structure_{index}',
        ))

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
    logger.info('ADULT_ANATOMY_QA_STARTED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s reviewer_count=%s required_reviewers=%s attempts_per_model=%s artifact_checksum_prefix=%s', user_id, job_id, request_chain_id, anatomical_profile, None, [], len(review_plan), required_reviewers, attempts_per_model, checksum)
    results=[]

    for model, review_prompt, phase in review_plan:
        prompt=review_prompt + "\\nSchema: " + ADULT_ANATOMY_QA_SCHEMA + "\\nRequirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)
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

        if not _provider_transient_anatomy_result(reviewer_result) and not reviewer_result.passed:
            logger.info('ADULT_ANATOMY_QA_CONCLUSIVE_REJECTION user_id=%s job_id=%s request_chain_id=%s qa_model=%s reason_codes=%s artifact_checksum_prefix=%s', user_id, job_id, request_chain_id, model, reviewer_result.reason_codes, checksum)
            break
        successful_count=sum(
            1 for result in results
            if result.passed and result.confidence in {'medium','high'}
        )
        if successful_count >= required_reviewers:
            break

    result=merge_adult_anatomy_reviewer_pool(
        results,
        required_reviewers=required_reviewers,
    )
    logger.info('ADULT_ANATOMY_QA_COMPLETED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s reviewer_count=%s successful_reviewers=%s required_reviewers=%s artifact_checksum_prefix=%s', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes, len(results), len(getattr(result, 'qa_passes', []) or []), required_reviewers, checksum)
    logger.info('ADULT_ANATOMY_QA_%s user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', 'PASSED' if result.passed else 'FAILED', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    return result
'''
replace_between(
    "app/services/generated_image_qa_service.py",
    "async def evaluate_adult_anatomy_image",
    "def metadata_has_valid_generated_image_qa",
    anatomy_function,
)
