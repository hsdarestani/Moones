from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
old_router = '''def enforce_relative_previous_image_reference(
    context: SemanticImageRouterContext,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Resolve «همین/همون قبلی» against the actual latest sent job.

    A requested scene/body/pose change is a refinement, never an exact resend.
    """
    if decision.action not in {
        SemanticImageAction.REFINE_PREVIOUS,
        SemanticImageAction.VARIATION,
        SemanticImageAction.RESEND_EXACT,
    }:
        return decision
    normalized = _norm_intent_text(context.current_user_message)
    if not any(marker in normalized for marker in ("قبلی", "همین عکس", "همون عکس", "همین قبلی", "همون قبلی", "همونو")):
        return decision
    latest = context.recent_image_job or context.latest_image_job
    if latest is None or latest.job_id is None:
        return decision
    modification_markers = (
        "کافه", "خونه", "خانه", "خیابون", "خیابان", "پارک", "ماشین", "مبل", "تخت", "اتاق", "حموم", "حمام",
        "لباس", "لخت", "قدی", "تمام قد", "نشسته", "ایستاده", "خوابیده", "دراز", "زاویه", "نور", "پس زمینه", "پس‌زمینه",
    )
    if any(marker in normalized for marker in modification_markers):
        decision.action = SemanticImageAction.REFINE_PREVIOUS
        decision.reason_code = "relative_previous_image_with_visual_change"
    decision.source_reference = SemanticSourceReference(kind="latest_image", job_id=latest.job_id)
    decision.media_delivery_requested = True
    decision.needs_clarification = False
    return decision
'''
new_router = '''def enforce_relative_previous_image_reference(
    context: SemanticImageRouterContext,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Resolve an explicit relative previous-image delivery request against the actual latest sent job.

    A requested scene/body/pose change is a refinement, never an exact resend. The
    invariant also recovers when the semantic model incorrectly classified the command
    as generate_new or clarify.
    """
    normalized = _norm_intent_text(context.current_user_message)
    previous_reference = any(marker in normalized for marker in ("قبلی", "همین عکس", "همون عکس", "همین قبلی", "همون قبلی", "همونو"))
    delivery_requested = any(marker in normalized for marker in ("بده", "بدی", "بفرست", "بفرستی", "بساز", "بگیر", "تغییر بده", "عوض کن", "درست کن"))
    if not (previous_reference and delivery_requested):
        return decision
    latest = context.recent_image_job or context.latest_image_job
    if latest is None or latest.job_id is None or str(latest.status or "") != "sent":
        return decision
    modification_markers = (
        "کافه", "خونه", "خانه", "خیابون", "خیابان", "پارک", "ماشین", "مبل", "تخت", "اتاق", "حموم", "حمام",
        "لباس", "لخت", "قدی", "تمام قد", "نشسته", "ایستاده", "خوابیده", "دراز", "زاویه", "نور", "پس زمینه", "پس‌زمینه",
    )
    has_visual_change = any(marker in normalized for marker in modification_markers)
    if has_visual_change or decision.action not in {
        SemanticImageAction.REFINE_PREVIOUS,
        SemanticImageAction.VARIATION,
        SemanticImageAction.RESEND_EXACT,
    }:
        decision.action = SemanticImageAction.REFINE_PREVIOUS
        decision.reason_code = "relative_previous_image_with_visual_change" if has_visual_change else "relative_previous_image_action_recovered"
    decision.source_reference = SemanticSourceReference(kind="latest_image", job_id=latest.job_id)
    decision.media_delivery_requested = True
    decision.needs_clarification = False
    logger.info("IMAGE_RELATIVE_PREVIOUS_REFERENCE_LOCKED action=%s job_id=%s", decision.action, latest.job_id)
    return decision
'''
router = replace_once(router, old_router, new_router, "relative previous recovery")
router_path.write_text(router)

pipeline_path = Path("app/services/image_pipeline_v2.py")
pipeline = pipeline_path.read_text()
pipeline = replace_once(
    pipeline,
    '''    vr.visibility_targets.partner_visible=vr.partner_visible
''',
    '''    explicit_body_regions=[name for name, region in (intent.body_visibility.regions or {}).items() if getattr(region, 'visibility_requested', False) or getattr(region, 'framing_requested', False)]
    vr.required_body_regions=list(dict.fromkeys(vr.required_body_regions + explicit_body_regions))
    vr.visibility_targets.partner_visible=vr.partner_visible
''',
    "body regions into visual requirements",
)
pipeline_path.write_text(pipeline)

qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text()
qa = replace_once(
    qa,
    "When explicit_nudity_requested is true, set requested_nudity_visible=true only when the requested nude/body visibility is actually visible; clothing or covered requested regions must set it false.",
    "When explicit_nudity_requested is true, inspect the actual pixels rather than the request text: set requested_nudity_visible=true only when the requested nudity is visibly fulfilled and every requested_body_regions item is actually visible. A clothed, covered, lingerie-only, cropped, or implied substitute must set it false. Never copy true merely because the example schema contains true.",
    "primary QA pixel evidence",
)
qa = replace_once(
    qa,
    "COMPACT_QA_PROMPT='''You are a compact fail-closed visual QA reviewer. Return one JSON object only; no prose and no real-person identification. Verify",
    "COMPACT_QA_PROMPT='''You are a compact fail-closed visual QA reviewer. Return one JSON object only; no prose and no real-person identification. When explicit_nudity_requested is true, inspect the actual pixels: requested_nudity_visible is true only when the requested nudity and every requested_body_regions item are visibly fulfilled; clothed, covered, lingerie-only, cropped, or implied substitutes are false. Never copy true from the example schema without visual evidence. Verify",
    "compact QA pixel evidence",
)
qa = replace_once(
    qa,
    '''    if codes & {'identity_inconsistent'}:
''',
    '''    if 'requested_nudity_missing' in codes:
        lines.append('Do not return a clothed, covered, lingerie-only, cropped, or implied substitute. Visibly fulfill the requested adult nudity level and every requested body region in natural framing.')
    if codes & {'identity_inconsistent'}:
''',
    "adult corrective retry",
)
qa_path.write_text(qa)

test_path = Path("tests/test_post_175_image_hardening.py")
test_path.write_text('''def _decision(action):
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
''')
