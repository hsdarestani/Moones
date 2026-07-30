from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
router = replace_once(
    router,
    '''    previous_markers = ("همین قبلی", "همون قبلی", "عکس قبلی", "همین عکس", "همون عکس", "همونو", "قبلی رو")
    if (
        decision.action in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}
        and any(marker in normalized for marker in previous_markers)
''',
    '''    previous_markers = ("همین قبلی", "همون قبلی", "عکس قبلی", "همین عکس", "همون عکس", "همونو", "قبلی رو")
    references_previous = any(marker in normalized for marker in previous_markers)
    previous_delivery_requested = references_previous and any(marker in normalized for marker in ("بده", "بدی", "بفرست", "بفرستی", "بساز", "بگیر", "تغییر بده", "عوض کن", "درست کن"))
    if (
        previous_delivery_requested
        and decision.action not in {SemanticImageAction.RESEND_EXACT, SemanticImageAction.VARIATION, SemanticImageAction.REFINE_PREVIOUS}
        and latest is not None
        and latest.job_id is not None
        and str(latest.status or "") == "sent"
    ):
        decision.action = SemanticImageAction.REFINE_PREVIOUS
        decision.media_delivery_requested = True
        decision.needs_clarification = False
        decision.reason_code = "explicit_previous_image_action_recovered"
    if (
        decision.action in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}
        and references_previous
''',
    "recover previous-image action",
)
router_path.write_text(router)

qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text()
qa = replace_once(
    qa,
    "QA_PROMPT='''You are a fail-closed visual fulfillment and realism QA module for photos shared by a persistent fictional adult partner. Return JSON only.",
    "QA_PROMPT='''You are a fail-closed visual fulfillment and realism QA module for photos shared by a persistent fictional adult partner. Return JSON only. When explicit_nudity_requested is true, inspect the pixels rather than the request text: set requested_nudity_visible=true only when the requested nudity is visibly fulfilled, and set required_body_regions_visible=true only when every requested body region is actually visible. A clothed, covered, lingerie-only, cropped, or implied substitute must return false. Never default either field to true merely because it appears in the schema. ",
    "full QA adult inspection instruction",
)
qa = replace_once(
    qa,
    "COMPACT_QA_PROMPT='''You are a compact fail-closed visual QA reviewer. Return one JSON object only; no prose and no real-person identification.",
    "COMPACT_QA_PROMPT='''You are a compact fail-closed visual QA reviewer. Return one JSON object only; no prose and no real-person identification. When explicit_nudity_requested is true, judge the actual pixels: requested_nudity_visible is true only for visibly fulfilled requested nudity, and required_body_regions_visible is true only when all requested regions are visible; clothed, covered, lingerie-only, cropped, or implied substitutes are false. Never copy true from the schema without visual evidence. ",
    "compact QA adult inspection instruction",
)
qa = replace_once(
    qa,
    '''    if vr.get('explicit_nudity_requested') or (vr.get('must_satisfy') or {}).get('explicit_nudity_requested'):
        required.extend(['requested_nudity_visible','required_body_regions_visible'])
''',
    '''    requested_regions=vr.get('required_body_regions') or (vr.get('must_satisfy') or {}).get('required_body_regions') or []
    if vr.get('explicit_nudity_requested') or (vr.get('must_satisfy') or {}).get('explicit_nudity_requested'):
        required.append('requested_nudity_visible')
    if requested_regions:
        required.append('required_body_regions_visible')
''',
    "QA mandatory body fields",
)
qa_path.write_text(qa)

test_path = Path("tests/test_image_fulfillment_continuity.py")
tests = test_path.read_text()
tests += '''\n\ndef test_previous_reference_recovers_from_wrong_generate_new_action():\n    from app.services.semantic_image_intent_router import RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext, enforce_previous_image_and_scene_continuity\n    context=SemanticImageRouterContext(current_user_message="همین قبلی رو تو کافه بده",recent_image_job=RecentImageJobSummary(job_id=88,status="sent",has_retrievable_artifact=False))\n    result=enforce_previous_image_and_scene_continuity(context,context.current_user_message,_decision(SemanticImageAction.GENERATE_NEW))\n    assert result.action == SemanticImageAction.REFINE_PREVIOUS\n    assert result.source_reference.job_id == 88\n    assert result.visual_intent.location == "cafe"\n\n\ndef test_adult_qa_prompt_requires_pixel_evidence_not_schema_copy():\n    from app.services.generated_image_qa_service import _qa_prompt_with_requirements\n    prompt=_qa_prompt_with_requirements({"explicit_nudity_requested":True,"required_body_regions":["full_body"]})\n    assert "inspect the pixels" in prompt\n    assert "Never default either field to true" in prompt\n'''
test_path.write_text(tests)
