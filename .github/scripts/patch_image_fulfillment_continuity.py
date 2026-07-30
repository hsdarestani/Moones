from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


router_path = Path("app/services/semantic_image_intent_router.py")
router = router_path.read_text()
anchor = '''def enforce_partner_photo_defaults(
    context: SemanticImageRouterContext,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
'''
helper = r'''_DETERMINISTIC_SCENE_ALIASES = (
    ("خونه", "home", "home", "private_indoor", "private"),
    ("خانه", "home", "home", "private_indoor", "private"),
    ("کافه", "cafe", "cafe", "public_indoor", "public"),
    ("خیابون", "street", "street", "public_outdoor", "public"),
    ("خیابان", "street", "street", "public_outdoor", "public"),
    ("پارک", "park", "park", "public_outdoor", "public"),
    ("ماشین", "car", "car", "vehicle", "private"),
)
_FUTURE_TRAVEL_MARKERS = ("میرسم", "می رسم", "برسم", "دارم میرم", "دارم می روم", "بذار برسم", "بزار برسم")
_ARRIVAL_MARKERS = ("رسیدم", "رسیدی", "رسیده", "رسید", "الان اونجام", "الان اینجام")


def _deterministic_scene_match(text: str):
    normalized = _norm_intent_text(text)
    return next((row for row in _DETERMINISTIC_SCENE_ALIASES if row[0] in normalized), None)


def enforce_previous_image_and_scene_continuity(
    context: SemanticImageRouterContext,
    current_text: str,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Lock explicit previous-image references and deterministic conversational location state."""
    normalized = _norm_intent_text(current_text)
    latest = context.recent_image_job or context.latest_image_job
    previous_markers = ("همین قبلی", "همون قبلی", "عکس قبلی", "همین عکس", "همون عکس", "همونو", "قبلی رو")
    if (
        decision.action in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}
        and any(marker in normalized for marker in previous_markers)
        and latest is not None
        and latest.job_id is not None
        and str(latest.status or "") == "sent"
        and (decision.action != SemanticImageAction.RESEND_EXACT or latest.has_retrievable_artifact)
    ):
        decision.source_reference = SemanticSourceReference(kind="latest_image", job_id=latest.job_id)
        decision.media_delivery_requested = True
        decision.needs_clarification = False
        decision.reason_code = "explicit_previous_image_locked_to_latest"
        logger.info("IMAGE_PREVIOUS_REFERENCE_LOCKED job_id=%s action=%s", latest.job_id, decision.action)

    if decision.action not in {SemanticImageAction.GENERATE_NEW, SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION} or not decision.media_delivery_requested:
        return decision

    visual = decision.visual_intent
    explicit = _deterministic_scene_match(current_text)
    if explicit:
        _, scene, location, environment_type, privacy = explicit
        visual.scene = scene
        visual.location = location
        visual.environment_type = environment_type
        visual.privacy = privacy
        visual.scene_explicit_current_request = True
        visual.current_scene_from_chat = False
        visual.scene_context_summary = None
        logger.info("IMAGE_EXPLICIT_SCENE_DETERMINISTICALLY_LOCKED scene=%s location=%s", scene, location)
        return decision

    if visual.scene_explicit_current_request or (visual.current_scene_from_chat and str(visual.scene_context_summary or "").strip()):
        return decision

    candidate = None
    candidate_index = -1
    candidate_was_future = False
    confirmed = False
    turns = list(context.recent_conversation or [])[-10:]
    for index, turn in enumerate(turns):
        turn_text = _norm_intent_text(getattr(turn, "text_summary", "") or "")
        match = _deterministic_scene_match(turn_text)
        if match:
            candidate = match
            candidate_index = index
            candidate_was_future = any(marker in turn_text for marker in _FUTURE_TRAVEL_MARKERS)
            confirmed = not candidate_was_future
            if any(marker in turn_text for marker in ("الان", "اینجا", "اونجام", "اینجام", "رسیدم")):
                confirmed = True
        if candidate is not None and index > candidate_index and any(marker in turn_text for marker in _ARRIVAL_MARKERS):
            confirmed = True

    if candidate is None or not confirmed:
        return decision
    _, scene, location, environment_type, privacy = candidate
    visual.scene = scene
    visual.location = location
    visual.environment_type = environment_type
    visual.privacy = privacy
    visual.current_scene_from_chat = True
    visual.scene_context_summary = f"the partner is currently at {location}"
    scene_constraint = "Keep the photo in the partner's deterministically resolved current location: " + visual.scene_context_summary
    if scene_constraint not in visual.freeform_visual_constraints:
        visual.freeform_visual_constraints.append(scene_constraint)
    logger.info("IMAGE_CONVERSATION_SCENE_DETERMINISTICALLY_LOCKED scene=%s location=%s", scene, location)
    return decision


'''
router = replace_once(router, anchor, helper + anchor, "scene/previous helper")
router_path.write_text(router)

telegram_path = Path("app/api/telegram.py")
telegram = telegram_path.read_text()
telegram = replace_once(
    telegram,
    '    enforce_referenced_object_request, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n',
    '    enforce_referenced_object_request, enforce_partner_photo_defaults, enforce_previous_image_and_scene_continuity, supersede_pending_image_clarification,\n',
    "telegram continuity import",
)
telegram = replace_once(
    telegram,
    '''        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)\n        semantic_decision = enforce_partner_photo_defaults(context, semantic_decision)\n''',
    '''        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)\n        semantic_decision = enforce_previous_image_and_scene_continuity(context, text, semantic_decision)\n        semantic_decision = enforce_partner_photo_defaults(context, semantic_decision)\n''',
    "telegram continuity wiring",
)
telegram_path.write_text(telegram)

pipeline_path = Path("app/services/image_pipeline_v2.py")
pipeline = pipeline_path.read_text()
pipeline = replace_once(
    pipeline,
    '''    vr.visibility_targets.partner_visible=vr.partner_visible\n''',
    '''    explicit_body_regions=[name for name, region in (intent.body_visibility.regions or {}).items() if getattr(region, 'visibility_requested', False) or getattr(region, 'framing_requested', False)]\n    vr.required_body_regions=list(dict.fromkeys(vr.required_body_regions + explicit_body_regions))\n    vr.visibility_targets.partner_visible=vr.partner_visible\n''',
    "adult required body regions",
)
pipeline = replace_once(
    pipeline,
    '''        'required_body_regions': vr.required_body_regions,\n        'forbidden_body_regions': vr.forbidden_body_regions,\n''',
    '''        'required_body_regions': vr.required_body_regions,\n        'forbidden_body_regions': vr.forbidden_body_regions,\n        'explicit_nudity_requested': intent.content_classification in {ContentClassification.TOPLESS, ContentClassification.FULL_NUDITY},\n        'adult_content_classification': str(intent.content_classification),\n''',
    "adult must satisfy metadata",
)
pipeline = replace_once(
    pipeline,
    '''        body_text=('full nudity, ' + visibility if content_classification.endswith('full_nudity') and visibility else (visibility or ('full nudity with the requested natural framing' if content_classification.endswith('full_nudity') else 'no explicit body emphasis')))\n        sections.append('Body visibility: ' + body_text + '.')\n''',
    '''        body_text=('full nudity, ' + visibility if content_classification.endswith('full_nudity') and visibility else (visibility or ('full nudity with the requested natural framing' if content_classification.endswith('full_nudity') else 'no explicit body emphasis')))\n        sections.append('Body visibility: ' + body_text + '.')\n        if content_classification.endswith('full_nudity'):\n            sections.append('Hard fulfillment requirement: the fictional adult subject must be visibly fully nude in the final image. Do not render a clothed, modestly covered, lingerie-only, or implied-nudity substitute. Required requested body regions must be actually visible within natural non-cropped framing.')\n        elif content_classification.endswith('topless'):\n            sections.append('Hard fulfillment requirement: the fictional adult subject must be visibly topless with the requested chest region actually uncovered. Do not substitute a shirt, bra, coat, dress, or implied coverage.')\n''',
    "adult hard positive prompt",
)
pipeline = replace_once(
    pipeline,
    '''    if allowed_adult_intent:\n        neg_terms.extend(['contradictory anatomy','mixed sex characteristics inconsistent with profile','malformed anatomy','ambiguous anatomy','duplicated body parts','anatomically inconsistent body','identity inconsistency'])\n''',
    '''    if allowed_adult_intent:\n        neg_terms.extend(['contradictory anatomy','mixed sex characteristics inconsistent with profile','malformed anatomy','ambiguous anatomy','duplicated body parts','anatomically inconsistent body','identity inconsistency'])\n        if content_classification.endswith('full_nudity'):\n            neg_terms.extend(['fully clothed','clothed body','shirt','bra','underwear','lingerie','dress','coat','trousers','modesty covering','implied nudity'])\n        elif content_classification.endswith('topless'):\n            neg_terms.extend(['shirt covering chest','bra','top covering breasts','coat covering chest','dress covering chest'])\n''',
    "adult clothing negative prompt",
)
pipeline_path.write_text(pipeline)

qa_path = Path("app/services/generated_image_qa_service.py")
qa = qa_path.read_text()
qa = replace_once(
    qa,
    "'visible_phone_in_non_mirror_selfie'\n}",
    "'visible_phone_in_non_mirror_selfie','requested_nudity_missing','requested_body_region_missing'\n}",
    "qa reason codes",
)
qa = replace_once(
    qa,
    '''    visible_held_phone_detected: bool = False\n''',
    '''    visible_held_phone_detected: bool = False\n    requested_nudity_visible: bool | None = None\n    required_body_regions_visible: bool | None = None\n''',
    "qa result adult fields",
)
qa = replace_once(
    qa,
    '"visible_held_phone_detected":false,"natural_capture_plausible":true',
    '"visible_held_phone_detected":false,"requested_nudity_visible":true,"required_body_regions_visible":true,"natural_capture_plausible":true',
    "full qa adult schema",
)
qa = replace_once(
    qa,
    '''        'scene_context_summary': (vr.get('photo_contract') or {}).get('scene_context_summary'),\n''',
    '''        'scene_context_summary': (vr.get('photo_contract') or {}).get('scene_context_summary'),\n        'explicit_nudity_requested': bool(vr.get('explicit_nudity_requested') or (vr.get('must_satisfy') or {}).get('explicit_nudity_requested')),\n        'required_body_regions': vr.get('required_body_regions') or (vr.get('must_satisfy') or {}).get('required_body_regions') or [],\n''',
    "full qa adult requirements",
)
qa = replace_once(
    qa,
    '"visible_held_phone_detected":false,"natural_capture_plausible":true',
    '"visible_held_phone_detected":false,"requested_nudity_visible":true,"required_body_regions_visible":true,"natural_capture_plausible":true',
    "compact qa adult schema",
)
qa = replace_once(
    qa,
    '''        'eye_contact_required': bool(vr.get('eye_contact_required')),\n''',
    '''        'eye_contact_required': bool(vr.get('eye_contact_required')),\n        'explicit_nudity_requested': bool(vr.get('explicit_nudity_requested') or (vr.get('must_satisfy') or {}).get('explicit_nudity_requested')),\n        'required_body_regions': vr.get('required_body_regions') or (vr.get('must_satisfy') or {}).get('required_body_regions') or [],\n''',
    "compact qa adult requirements",
)
qa = replace_once(
    qa,
    '''    if contract.get('hands_only'):\n        required.append('hands_only_matches_request')\n''',
    '''    if contract.get('hands_only'):\n        required.append('hands_only_matches_request')\n    if vr.get('explicit_nudity_requested') or (vr.get('must_satisfy') or {}).get('explicit_nudity_requested'):\n        required.extend(['requested_nudity_visible','required_body_regions_visible'])\n''',
    "qa mandatory adult fields",
)
qa = replace_once(
    qa,
    '''    visible_held_phone=_bool(payload.get('visible_held_phone_detected'))\n''',
    '''    visible_held_phone=_bool(payload.get('visible_held_phone_detected'))\n    requested_nudity_visible=None if payload.get('requested_nudity_visible') is None else _bool(payload.get('requested_nudity_visible'))\n    required_body_regions_visible=None if payload.get('required_body_regions_visible') is None else _bool(payload.get('required_body_regions_visible'))\n    explicit_nudity_required=bool(vr.get('explicit_nudity_requested') or (vr.get('must_satisfy') or {}).get('explicit_nudity_requested'))\n    requested_body_regions=vr.get('required_body_regions') or (vr.get('must_satisfy') or {}).get('required_body_regions') or []\n    if explicit_nudity_required and requested_nudity_visible is not True: codes.append('requested_nudity_missing')\n    if requested_body_regions and required_body_regions_visible is not True: codes.append('requested_body_region_missing')\n''',
    "qa evaluate adult fulfillment",
)
qa = replace_once(
    qa,
    '''    result.visible_held_phone_detected=visible_held_phone\n''',
    '''    result.visible_held_phone_detected=visible_held_phone\n    result.requested_nudity_visible=requested_nudity_visible\n    result.required_body_regions_visible=required_body_regions_visible\n''',
    "qa result adult assignment",
)
qa = replace_once(
    qa,
    '''    if adult_strict:\n        aqa=metadata.get('adult_anatomy_qa') or {}\n''',
    '''    if adult_strict:\n        if qa.get('requested_nudity_visible') is not True or qa.get('required_body_regions_visible') is not True: return False\n        aqa=metadata.get('adult_anatomy_qa') or {}\n''',
    "delivery adult fulfillment gate",
)
qa = replace_once(
    qa,
    '''    elif codes & {'primary_subject_mismatch','requested_pet_missing','required_object_missing','unexpected_visible_partner','face_should_be_hidden','face_should_be_visible','back_view_mismatch','camera_mode_mismatch','implausible_camera_capture','id_photo_regression','hands_only_mismatch','selfie_required','selfie_geometry_inconsistent','third_person_viewpoint','visible_phone_in_non_mirror_selfie'}:\n''',
    '''    elif codes & {'requested_nudity_missing','requested_body_region_missing'}:\n        msg='این بار پوشش و جزئیات بدنی مطابق چیزی که خواستی درنیومد؛ عکس ارسال نشد و سکه‌ات برگشت.'\n    elif codes & {'primary_subject_mismatch','requested_pet_missing','required_object_missing','unexpected_visible_partner','face_should_be_hidden','face_should_be_visible','back_view_mismatch','camera_mode_mismatch','implausible_camera_capture','id_photo_regression','hands_only_mismatch','selfie_required','selfie_geometry_inconsistent','third_person_viewpoint','visible_phone_in_non_mirror_selfie'}:\n''',
    "adult qa user message",
)
qa = replace_once(
    qa,
    '''    if codes & {'identity_inconsistent'}:\n''',
    '''    if codes & {'requested_nudity_missing','requested_body_region_missing'}:\n        lines.append('Do not return a clothed or implied substitute. Fulfill the requested adult nudity level and make every requested body region actually visible in natural framing.')\n    if codes & {'identity_inconsistent'}:\n''',
    "adult corrective prompt",
)
qa_path.write_text(qa)

test_path = Path("tests/test_image_fulfillment_continuity.py")
test_path.write_text(r'''def _decision(action="generate_new"):
    from app.services.semantic_image_intent_router import SemanticImageDecision, VisualIntent
    return SemanticImageDecision(action=action,media_delivery_requested=True,confidence=1.0,reason_code="test",needs_clarification=False,visual_intent=VisualIntent(primary_subject="partner",partner_visible=True))


def test_previous_reference_is_locked_to_latest_sent_job():
    from app.services.semantic_image_intent_router import RecentImageJobSummary, SemanticImageAction, SemanticImageRouterContext, enforce_previous_image_and_scene_continuity
    context=SemanticImageRouterContext(current_user_message="همین قبلی رو تو کافه بده",recent_image_job=RecentImageJobSummary(job_id=77,status="sent",has_retrievable_artifact=True))
    result=enforce_previous_image_and_scene_continuity(context,context.current_user_message,_decision(SemanticImageAction.REFINE_PREVIOUS))
    assert result.source_reference.job_id == 77
    assert result.visual_intent.scene == "cafe"
    assert result.visual_intent.scene_explicit_current_request is True


def test_future_home_transition_requires_arrival_then_becomes_current_scene():
    from app.services.semantic_image_intent_router import ConversationTurnSummary, SemanticImageRouterContext, enforce_previous_image_and_scene_continuity
    context=SemanticImageRouterContext(current_user_message="خب بفرست عکس همه هاتو",recent_conversation=[ConversationTurnSummary(role="assistant",text_summary="آروم میرسم خونه"),ConversationTurnSummary(role="user",text_summary="رسیدی فک کنم")])
    result=enforce_previous_image_and_scene_continuity(context,context.current_user_message,_decision())
    assert result.visual_intent.location == "home"
    assert result.visual_intent.environment_type == "private_indoor"
    assert result.visual_intent.current_scene_from_chat is True


def test_full_nudity_intent_exports_required_body_regions():
    from app.services import image_pipeline_v2 as v2
    from app.services.image_generation_guardrails import apply_deterministic_adult_visual_intent
    intent=v2.parse_image_intent(v2.normalize_request_v2("یه عکس لختی بده بدنتو ببینم"))
    intent=apply_deterministic_adult_visual_intent(intent,"یه عکس لختی بده بدنتو ببینم")
    assert intent.content_classification == v2.ContentClassification.FULL_NUDITY
    vr=v2.resolve_visual_requirements(intent,user_request="یه عکس لختی بده بدنتو ببینم")
    assert {"breasts","buttocks","full_body"}.issubset(set(vr.required_body_regions))


def test_qa_rejects_clothed_result_for_explicit_nudity():
    from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload
    payload={"person_count":1,"face_count":1,"intended_subject_count":1,"confidence":"high","framing":"full_body","framing_matches_request":True,"head_inside_frame":True,"feet_inside_frame":True,"body_not_cropped":True,"requested_scene_visible":True,"requested_support_surface_visible":True,"requested_pose_matches":True,"identity_consistency_reasonable":True,"primary_subject_matches_request":True,"partner_visible":True,"face_visible":True,"camera_mode_matches_request":True,"natural_capture_plausible":True,"looks_like_id_photo":False,"requested_nudity_visible":False,"required_body_regions_visible":False,"reason_codes":[]}
    vr={"explicit_nudity_requested":True,"required_body_regions":["breasts","buttocks","full_body"],"framing_requirement":"full_body","full_body_visible":True,"photo_contract":{"partner_visible":True,"natural_capture_required":True}}
    result=evaluate_generated_image_composition_payload(payload,expected_subject_count=1,visual_requirements=vr)
    assert result.passed is False
    assert "requested_nudity_missing" in result.reason_codes
    assert "requested_body_region_missing" in result.reason_codes
''')
