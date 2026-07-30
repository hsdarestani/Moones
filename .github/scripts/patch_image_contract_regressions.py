from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# 1) Adult intent, contextual follow-up, and private-scene override.
guard_path = Path("app/services/image_generation_guardrails.py")
guard = guard_path.read_text()
guard = replace_once(
    guard,
    '_PUBLIC_PRIVACY_VALUES = {"public", "public_outdoor", "public_indoor", "street", "cafe", "park"}\n',
    '_PUBLIC_PRIVACY_VALUES = {"public", "public_outdoor", "public_indoor", "street", "cafe", "park"}\n\n\ndef _normalize_fa_text(value: object) -> str:\n    return " ".join(str(value or "").replace("‌", " ").replace("ي", "ی").replace("ك", "ک").lower().split())\n',
    "guard normalization helper",
)
guard = replace_once(
    guard,
    '    text = " ".join(str(user_text or "").replace("‌", " ").replace("ي", "ی").replace("ك", "ک").lower().split())\n',
    '    text = _normalize_fa_text(user_text)\n',
    "guard deterministic normalization",
)
anchor = '\n\ndef apply_adult_scene_policy(intent, routine_context: dict[str, Any] | None) -> AdultScenePolicyResult:\n'
inherit = '''\n\ndef inherit_recent_adult_visual_intent(intent, user_text: str, recent_conversation):
    """Carry an immediately preceding explicit adult request into a body-referential photo follow-up.

    This is intentionally narrow: ordinary requests such as «عکس قدی بده» never inherit nudity.
    """
    from app.services import image_pipeline_v2 as v2

    if str(intent.content_classification) in {
        str(v2.ContentClassification.TOPLESS),
        str(v2.ContentClassification.FULL_NUDITY),
    }:
        return intent
    text = _normalize_fa_text(user_text)
    delivery = any(term in text for term in ("عکس", "سلفی", "بده", "بدی", "بفرست", "بفرس", "بگیر", "ببینم"))
    body_followup = any(term in text for term in ("همه جات", "همه جاتو", "همه هات", "همه هاتو", "کل بدنت", "بدنتو", "بدنت رو", "سرتاپات"))
    if not (delivery and body_followup):
        return intent
    for message in reversed(list(recent_conversation or [])[-12:]):
        if str(getattr(message, "role", "") or "") != "user":
            continue
        prior_text = _normalize_fa_text(getattr(message, "content", ""))
        if prior_text == text:
            continue
        if any(term in prior_text for term in ("لخت", "لختی", "برهنه", "بدون لباس", "ممه", "سینه")):
            inherited = apply_deterministic_adult_visual_intent(intent, prior_text)
            if str(inherited.content_classification) in {
                str(v2.ContentClassification.TOPLESS),
                str(v2.ContentClassification.FULL_NUDITY),
            }:
                return inherited
    return intent
'''
if anchor not in guard:
    raise RuntimeError("adult scene policy anchor missing")
guard = guard.replace(anchor, inherit + anchor, 1)
old_scene = '''    intent.scene.scene_key = "private_indoor"
    intent.scene.location = "private indoor setting"
    intent.scene.environment_type = "private_indoor"
    intent.scene.privacy = "private"
    intent.scene.required_visible_environment_elements = ["private indoor environment"]
    safe_routine = dict(routine_context or {})
    safe_routine["location"] = None
    safe_routine["scene"] = None
    safe_routine["environment_type"] = None
    return AdultScenePolicyResult(routine_context=safe_routine, private_scene_applied=True)
'''
new_scene = '''    intent.scene.scene_key = "private_indoor"
    intent.scene.location = "private indoor setting"
    intent.scene.environment_type = "private_indoor"
    intent.scene.privacy = "private"
    intent.scene.required_visible_environment_elements = ["private indoor environment"]
    # A prior cafe/street contract must never survive a private adult-scene override.
    contract = dict(getattr(intent, "photo_contract", {}) or {})
    contract["current_scene_from_chat"] = False
    contract["scene_context_summary"] = None
    intent.photo_contract = contract
    stale_scene_prefixes = (
        "current scene and activity:",
        "keep the photo in the partner's semantically resolved current location",
    )
    intent.passthrough_visual_details = [
        item for item in list(getattr(intent, "passthrough_visual_details", []) or [])
        if not _normalize_fa_text(item).startswith(stale_scene_prefixes)
    ]
    if getattr(intent, "parse_coverage", None) is not None:
        intent.parse_coverage.passthrough_visual_spans = [
            item for item in list(intent.parse_coverage.passthrough_visual_spans or [])
            if not _normalize_fa_text(item).startswith(stale_scene_prefixes)
        ]
    safe_routine = dict(routine_context or {})
    safe_routine["location"] = None
    safe_routine["scene"] = None
    safe_routine["environment_type"] = None
    return AdultScenePolicyResult(routine_context=safe_routine, private_scene_applied=True)
'''
guard = replace_once(guard, old_scene, new_scene, "private adult scene override")
guard_path.write_text(guard)


# 2) Use contextual adult follow-up after recent conversation is loaded.
service_path = Path("app/services/image_generation_service.py")
service = service_path.read_text()
service = replace_once(
    service,
    'from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_deterministic_adult_visual_intent, apply_adult_scene_policy, select_generation_model\n',
    'from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_deterministic_adult_visual_intent, inherit_recent_adult_visual_intent, apply_adult_scene_policy, select_generation_model\n',
    "image service guard import",
)
service = replace_once(
    service,
    '    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)\n    intent=inherit_recent_image_scene(intent, recent_conversation)\n',
    '    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)\n    intent=inherit_recent_adult_visual_intent(intent, user_request, recent_conversation)\n    intent=inherit_recent_image_scene(intent, recent_conversation)\n',
    "contextual adult follow-up wiring",
)
service_path.write_text(service)


# 3) Hard fulfillment and negative constraints for requested nudity.
pipeline_path = Path("app/services/image_pipeline_v2.py")
pipeline = pipeline_path.read_text()
pipeline = replace_once(
    pipeline,
    "        body_text=('full nudity, ' + visibility if content_classification.endswith('full_nudity') and visibility else (visibility or ('full nudity with the requested natural framing' if content_classification.endswith('full_nudity') else 'no explicit body emphasis')))\n        sections.append('Body visibility: ' + body_text + '.')\n",
    "        body_text=('full nudity, ' + visibility if content_classification.endswith('full_nudity') and visibility else (visibility or ('full nudity with the requested natural framing' if content_classification.endswith('full_nudity') else 'no explicit body emphasis')))\n        sections.append('Body visibility: ' + body_text + '.')\n        if content_classification.endswith('full_nudity'):\n            sections.append('Hard fulfillment requirement: the fictional adult is visibly fully nude in the requested natural framing; clothing, underwear, lingerie, shirts, dresses, coats, or covered requested body regions do not satisfy the request.')\n        elif content_classification.endswith('topless'):\n            sections.append('Hard fulfillment requirement: the requested fictional adult upper-body nudity is visibly fulfilled; clothing covering the requested chest region does not satisfy the request.')\n",
    "adult prompt fulfillment",
)
pipeline = replace_once(
    pipeline,
    "    if allowed_adult_intent:\n        neg_terms.extend(['contradictory anatomy','mixed sex characteristics inconsistent with profile','malformed anatomy','ambiguous anatomy','duplicated body parts','anatomically inconsistent body','identity inconsistency'])\n",
    "    if allowed_adult_intent:\n        neg_terms.extend(['contradictory anatomy','mixed sex characteristics inconsistent with profile','malformed anatomy','ambiguous anatomy','duplicated body parts','anatomically inconsistent body','identity inconsistency'])\n        if content_classification.endswith('full_nudity'):\n            neg_terms.extend(['clothed body','shirt','t-shirt','dress','coat','jacket','underwear','lingerie','covered torso','covered breasts'])\n        elif content_classification.endswith('topless'):\n            neg_terms.extend(['shirt covering chest','top covering chest','covered breasts'])\n",
    "adult negative prompt fulfillment",
)
pipeline = replace_once(
    pipeline,
    "    if getattr(plan.visual_requirements, 'framing_requirement', None) == 'full_body':\n",
    "    if str(plan.current_intent.get('content_classification') or '').lower().endswith('full_nudity'):\n        if 'Hard fulfillment requirement: the fictional adult is visibly fully nude' not in positive:\n            errors.append(str(InvariantCode.PROMPT_CONTRADICTION))\n        if any(term not in compiled.negative_prompt for term in ['clothed body','shirt','underwear','covered torso']):\n            errors.append(str(InvariantCode.PROMPT_CONTRADICTION))\n    if getattr(plan.visual_requirements, 'framing_requirement', None) == 'full_body':\n",
    "adult compiled prompt invariant",
)
pipeline_path.write_text(pipeline)


# 4) QA must reject clothed/covered output for an explicit nudity request.
qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text()
qa = replace_once(
    qa,
    "'requested_clothing_not_visible','requested_scene_not_visible'",
    "'requested_clothing_not_visible','requested_nudity_missing','requested_scene_not_visible'",
    "QA reason code",
)
qa = replace_once(
    qa,
    '    requested_clothing_visible: bool | None = None\n    requested_scene_visible: bool | None = None\n',
    '    requested_clothing_visible: bool | None = None\n    requested_nudity_visible: bool | None = None\n    requested_scene_visible: bool | None = None\n',
    "QA result nudity field",
)
qa = replace_once(
    qa,
    'Check the requested primary subject, required objects or pet, partner visibility, face shown or hidden, back-facing pose, framing, scene, camera method, and whether the capture is physically plausible.',
    'Check the requested primary subject, required objects or pet, partner visibility, face shown or hidden, back-facing pose, framing, scene, camera method, and whether the capture is physically plausible. When explicit_nudity_requested is true, set requested_nudity_visible=true only when the requested nude/body visibility is actually visible; clothing or covered requested regions must set it false.',
    "QA prompt nudity instruction",
)
qa = replace_once(
    qa,
    '"requested_scene_visible":true,',
    '"requested_nudity_visible":true,"requested_scene_visible":true,',
    "QA schema nudity field",
)
qa = replace_once(
    qa,
    '"requested_clothing_visible":true,"no_clothing_regression":true,',
    '"requested_clothing_visible":true,"requested_nudity_visible":true,"no_clothing_regression":true,',
    "compact QA schema nudity field",
)
qa = replace_once(
    qa,
    "        'identity_consistency_required': bool((vr.get('photo_contract') or {}).get('identity_consistency_required')),\n",
    "        'identity_consistency_required': bool((vr.get('photo_contract') or {}).get('identity_consistency_required')),\n        'explicit_nudity_requested': bool(vr.get('explicit_nudity_requested')),\n        'requested_body_regions': vr.get('required_body_regions') or list((vr.get('must_satisfy') or {}).get('required_body_regions') or []),\n",
    "QA requirements nudity fields",
)
qa = replace_once(
    qa,
    "        'eye_contact_required': bool(vr.get('eye_contact_required')),\n",
    "        'eye_contact_required': bool(vr.get('eye_contact_required')),\n        'explicit_nudity_requested': bool(vr.get('explicit_nudity_requested')),\n        'requested_body_regions': vr.get('required_body_regions') or list((vr.get('must_satisfy') or {}).get('required_body_regions') or []),\n",
    "compact QA requirements nudity fields",
)
qa = replace_once(
    qa,
    "    if contract.get('hands_only'):\n        required.append('hands_only_matches_request')\n",
    "    if contract.get('hands_only'):\n        required.append('hands_only_matches_request')\n    if vr.get('explicit_nudity_requested'):\n        required.append('requested_nudity_visible')\n",
    "QA required nudity field",
)
qa = replace_once(
    qa,
    "    requested_clothing_visible=None if payload.get('requested_clothing_visible') is None else _bool(payload.get('requested_clothing_visible'))\n    requested_scene_visible=None if payload.get('requested_scene_visible') is None else _bool(payload.get('requested_scene_visible'))\n",
    "    requested_clothing_visible=None if payload.get('requested_clothing_visible') is None else _bool(payload.get('requested_clothing_visible'))\n    requested_nudity_visible=None if payload.get('requested_nudity_visible') is None else _bool(payload.get('requested_nudity_visible'))\n    requested_scene_visible=None if payload.get('requested_scene_visible') is None else _bool(payload.get('requested_scene_visible'))\n",
    "QA payload nudity parse",
)
qa = replace_once(
    qa,
    "    if wardrobe_required and requested_clothing_visible is False: codes.append('requested_clothing_not_visible')\n",
    "    if wardrobe_required and requested_clothing_visible is False: codes.append('requested_clothing_not_visible')\n    if vr.get('explicit_nudity_requested') and requested_nudity_visible is not True: codes.append('requested_nudity_missing')\n",
    "QA nudity fulfillment failure",
)
qa = replace_once(
    qa,
    'requested_clothing_visible=requested_clothing_visible, requested_scene_visible=requested_scene_visible,',
    'requested_clothing_visible=requested_clothing_visible, requested_nudity_visible=requested_nudity_visible, requested_scene_visible=requested_scene_visible,',
    "QA result nudity assignment",
)
qa = replace_once(
    qa,
    "    adult_strict=bool(vr.get('explicit_nudity_requested') and vr.get('anatomy_qa_required'))\n",
    "    if vr.get('explicit_nudity_requested') and qa.get('requested_nudity_visible') is not True:\n        return False\n    adult_strict=bool(vr.get('explicit_nudity_requested') and vr.get('anatomy_qa_required'))\n",
    "delivery nudity gate",
)
qa = replace_once(
    qa,
    "    elif codes & {'framing_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden','anatomy_profile_missing'",
    "    elif 'requested_nudity_missing' in codes:\n        msg='این بار تصویر چیزی که خواستی نشد؛ عکس ارسال نشد و سکه‌ات برگشت.'\n    elif codes & {'framing_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden','anatomy_profile_missing'",
    "QA user message nudity failure",
)
qa_path.write_text(qa)


# 5) Prevent ordinary world-state chat from being hijacked by recent image status;
#    bind relative previous-image references to the actual latest image.
router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
status_anchor = '\n\nasync def resolve_active_image_job_followup_semantically(\n'
status_helper = '''\n\ndef image_job_followup_candidate(text: str) -> bool:
    """Cheap pre-gate for the costly image-job control classifier.

    World-state chat such as «رسیدی فک کنم» is not an image follow-up merely because a
    photo was recently sent.
    """
    normalized = _norm_intent_text(text)
    if not normalized:
        return False
    if any(term in normalized for term in ("عکس", "تصویر", "سلفی", "فتو")):
        return True
    if normalized.startswith(("چیشد", "چی شد", "پس چی شد")):
        return True
    compact_markers = ("هنوز نیومد", "هنوز نرسید", "آماده شد", "فرستادی", "لغوش کن", "نفرست", "بیخیالش")
    return len(normalized.split()) <= 6 and any(marker in normalized for marker in compact_markers)
'''
if status_anchor not in router:
    raise RuntimeError("status resolver anchor missing")
router = router.replace(status_anchor, status_helper + status_anchor, 1)
router = replace_once(
    router,
    "    if decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.CLARIFY}:\n        return decision\n    target=context.active_image_job\n",
    "    if decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.CLARIFY}:\n        return decision\n    if not image_job_followup_candidate(context.current_user_message):\n        logger.info('IMAGE_ACTIVE_JOB_FOLLOWUP_SKIPPED reason=no_image_status_surface')\n        return decision\n    target=context.active_image_job\n",
    "status follow-up pre-gate",
)
router = replace_once(
    router,
    "        'cancel_pending means the user wants the image stopped. chat means neither. Return JSON only: {\"action\":\"status_query|cancel_pending|chat\",\"confidence\":0.0}. Do not use phrase matching.'\n",
    "        'Messages about whether the partner arrived, left, went home, came back, or is tired are ordinary world-state chat unless they explicitly mention the photo. '\n        'cancel_pending means the user wants the image stopped. chat means neither. Return JSON only: {\"action\":\"status_query|cancel_pending|chat\",\"confidence\":0.0}. Do not use phrase matching.'\n",
    "status classifier world-state instruction",
)
relative_anchor = '\n\ndef enforce_partner_photo_defaults(\n'
relative_helper = '''\n\ndef enforce_relative_previous_image_reference(
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
if relative_anchor not in router:
    raise RuntimeError("partner defaults anchor missing")
router = router.replace(relative_anchor, relative_helper + relative_anchor, 1)
router_path.write_text(router)

telegram_path = Path("app/api/telegram.py")
telegram = telegram_path.read_text()
telegram = replace_once(
    telegram,
    '    enforce_referenced_object_request, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n',
    '    enforce_referenced_object_request, enforce_relative_previous_image_reference, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n',
    "telegram relative previous import",
)
telegram = replace_once(
    telegram,
    '        semantic_decision = enforce_referenced_object_request(context, deterministic_action, semantic_decision)\n        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)\n',
    '        semantic_decision = enforce_referenced_object_request(context, deterministic_action, semantic_decision)\n        semantic_decision = enforce_relative_previous_image_reference(context, semantic_decision)\n        semantic_decision = enforce_clarification_scope(text, pending_resolution, semantic_decision)\n',
    "telegram relative previous wiring",
)
telegram_path.write_text(telegram)


# Regression tests are provider-free and use no Venice image generation.
test_path = Path("tests/test_image_contract_regressions.py")
test_path.write_text(r'''import asyncio
from types import SimpleNamespace


def test_explicit_persian_nudity_is_locked_and_private_scene_clears_cafe_contract():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import (
        apply_adult_scene_policy,
        apply_deterministic_adult_visual_intent,
    )

    intent = v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))
    intent = apply_deterministic_adult_visual_intent(intent, "یه عکس لختی بده بدنتو ببینم")
    intent.photo_contract = {
        "current_scene_from_chat": True,
        "scene_context_summary": "the partner is outside at a cafe",
    }
    intent.passthrough_visual_details = ["current scene and activity: the partner is outside at a cafe"]
    result = apply_adult_scene_policy(intent, {"location": "cafe", "scene": "street"})

    assert str(intent.content_classification) == str(v2.ContentClassification.FULL_NUDITY)
    assert result.private_scene_applied is True
    assert intent.scene.scene_key == "private_indoor"
    assert intent.scene.privacy == "private"
    assert intent.photo_contract["current_scene_from_chat"] is False
    assert intent.photo_contract["scene_context_summary"] is None
    assert not intent.passthrough_visual_details


def test_body_referential_followup_inherits_recent_explicit_adult_request_only():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import inherit_recent_adult_visual_intent

    intent = v2.parse_image_intent(v2.normalize_request_v2("خب بفرس عکس همه هاتو"))
    recent = [SimpleNamespace(role="user", content="یه عکس لختی بده بدنتو ببینم")]
    inherited = inherit_recent_adult_visual_intent(intent, "خب بفرس عکس همه هاتو", recent)
    assert str(inherited.content_classification) == str(v2.ContentClassification.FULL_NUDITY)

    ordinary = v2.parse_image_intent(v2.normalize_request_v2("یه عکس قدی بده"))
    ordinary = inherit_recent_adult_visual_intent(ordinary, "یه عکس قدی بده", recent)
    assert str(ordinary.content_classification) != str(v2.ContentClassification.FULL_NUDITY)


def test_full_nudity_prompt_forbids_clothed_fallback():
    from app.services import image_pipeline_v2 as v2

    vr = v2.VisualRequirements(
        explicit_nudity_requested=True,
        anatomy_consistency_required=True,
        anatomical_profile="female",
        framing_requirement="full_body",
        full_body_visible=True,
        head_visible=True,
        feet_visible=True,
        body_not_cropped=True,
        photo_contract={"partner_visible": True, "identity_consistency_required": True},
    )
    plan = SimpleNamespace(
        identity={"descriptor": {}},
        current_intent={"content_classification": "full_nudity", "expression_modifiers": [], "explicit_exclusions": []},
        body_visibility={"full_body": {"visibility_requested": True, "framing_requested": True}},
        composition={"expected_subject_count": 1, "photo_contract": vr.photo_contract, "width": 1024, "height": 1280},
        visual_requirements=vr,
        continuity_plan=v2.ContinuityPlan(),
        action=v2.ImageAction.NEW_GENERATION,
        scene=v2.ResolvedField("private_indoor", v2.Provenance.EXPLICIT),
        location=v2.ResolvedField("private indoor setting", v2.Provenance.EXPLICIT),
        activity=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        pose=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        support_surface=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        wardrobe=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        camera=v2.ResolvedField("casual_selfie", v2.Provenance.EXPLICIT),
        lighting=v2.ResolvedField(None, v2.Provenance.SYSTEM),
        required_objects=v2.ResolvedField([], v2.Provenance.SYSTEM),
        excluded_objects=v2.ResolvedField([], v2.Provenance.SYSTEM),
        passthrough_visual_details=[],
        seed_strategy={"final_provider_seed": 7},
    )
    compiled = v2.compile_image_prompt(plan)
    assert "Hard fulfillment requirement: the fictional adult is visibly fully nude" in compiled.positive_prompt
    for term in ("clothed body", "shirt", "underwear", "covered torso"):
        assert term in compiled.negative_prompt


def _base_qa_payload(**updates):
    payload = {
        "person_count": 1,
        "face_count": 1,
        "intended_subject_count": 1,
        "confidence": "high",
        "framing": "full_body",
        "framing_matches_request": True,
        "full_body_visible": True,
        "head_inside_frame": True,
        "feet_inside_frame": True,
        "body_not_cropped": True,
        "requested_scene_visible": True,
        "requested_support_surface_visible": True,
        "requested_pose_matches": True,
        "requested_nudity_visible": False,
        "natural_capture_plausible": True,
        "looks_like_id_photo": False,
        "reason_codes": [],
    }
    payload.update(updates)
    return payload


def test_clothed_output_cannot_pass_explicit_nudity_qa_or_delivery_gate():
    from app.services.generated_image_qa_service import (
        evaluate_generated_image_composition_payload,
        metadata_has_valid_generated_image_qa,
    )

    requirements = {
        "explicit_nudity_requested": True,
        "framing_requirement": "full_body",
        "full_body_visible": True,
        "photo_contract": {},
    }
    result = evaluate_generated_image_composition_payload(
        _base_qa_payload(),
        expected_subject_count=1,
        visual_requirements=requirements,
    )
    assert result.passed is False
    assert "requested_nudity_missing" in result.reason_codes

    image = b"candidate"
    metadata = {
        "visual_requirements": requirements,
        "generated_image_qa": {
            **result.to_metadata(artifact_checksum=__import__("hashlib").sha256(image).hexdigest()),
            "passed": True,
            "requested_nudity_visible": False,
        },
    }
    assert metadata_has_valid_generated_image_qa(metadata, image) is False


def test_arrival_chat_is_not_reclassified_as_image_status_and_costs_no_control_call():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary,
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        VisualIntent,
        resolve_active_image_job_followup_semantically,
    )

    class Client:
        async def complete_result(self, *args, **kwargs):
            raise AssertionError("status control model must not be called")

    model = SimpleNamespace(client=Client(), model="test", timeout_seconds=1)
    context = SemanticImageRouterContext(
        current_user_message="رسیدی فک کنم",
        latest_image_job=RecentImageJobSummary(job_id=1, status="sent"),
        seconds_since_recent_image=30,
    )
    original = SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=.9,
        reason_code="ordinary_world_state_chat",
        visual_intent=VisualIntent(),
    )
    result = asyncio.run(resolve_active_image_job_followup_semantically(context, original, model=model))
    assert result is original
    assert result.action == SemanticImageAction.CHAT


def test_colloquial_what_happened_still_reaches_image_status_control():
    from app.services.semantic_image_intent_router import image_job_followup_candidate

    assert image_job_followup_candidate("چیشد پس") is True
    assert image_job_followup_candidate("عکس کو") is True
    assert image_job_followup_candidate("رسیدی فک کنم") is False


def test_relative_previous_image_with_cafe_change_uses_latest_as_refinement():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary,
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        SemanticSourceReference,
        VisualIntent,
        enforce_relative_previous_image_reference,
    )

    context = SemanticImageRouterContext(
        current_user_message="همین قبلی رو تو کافه بده",
        latest_image_job=RecentImageJobSummary(job_id=42, status="sent"),
    )
    decision = SemanticImageDecision(
        action=SemanticImageAction.RESEND_EXACT,
        media_delivery_requested=True,
        confidence=.9,
        reason_code="model_exact_resend",
        source_reference=SemanticSourceReference(kind="image_job", job_id=7),
        visual_intent=VisualIntent(location="cafe"),
    )
    result = enforce_relative_previous_image_reference(context, decision)
    assert result.action == SemanticImageAction.REFINE_PREVIOUS
    assert result.source_reference.job_id == 42
''')
