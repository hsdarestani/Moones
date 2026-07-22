from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label} target not found")
    return text.replace(old, new, 1)

# semantic router
path = Path('app/services/semantic_image_intent_router.py')
text = path.read_text(encoding='utf-8')
text = replace_once(
    text,
    "    natural_capture_required: bool = True\n",
    "    natural_capture_required: bool = True\n    current_scene_from_chat: bool = False\n    scene_context_summary: str | None = None\n    identity_continuity_required: bool = True\n",
    'visual intent continuity fields',
)
marker = "\n\n@dataclass\nclass ConversationTurnSummary:"
helper = '''\n\ndef enforce_partner_photo_defaults(\n    context: SemanticImageRouterContext,\n    decision: SemanticImageDecision,\n) -> SemanticImageDecision:\n    \"\"\"Apply product-level defaults for a real persistent partner photo.\n\n    The semantic model remains authoritative for explicit camera, framing, scene,\n    object, pet and body instructions. This only fills genuinely omitted fields.\n    \"\"\"\n    if decision.action != SemanticImageAction.GENERATE_NEW or not decision.media_delivery_requested:\n        return decision\n    visual = decision.visual_intent\n    primary = str(visual.primary_subject or \"partner\").strip().lower()\n    if (\n        primary not in {\"partner\", \"person\", \"self\"}\n        or visual.partner_visible is False\n        or visual.object_only\n        or visual.pet_only\n        or visual.hands_only\n    ):\n        return decision\n\n    visual.primary_subject = \"partner\"\n    visual.partner_visible = True\n    visual.natural_capture_required = True\n    visual.identity_continuity_required = True\n    if not visual.camera_mode:\n        if visual.back_to_camera:\n            visual.camera_mode = \"tripod_timer\"\n        elif visual.framing == \"full_body\":\n            visual.camera_mode = \"mirror_selfie\"\n        else:\n            visual.camera_mode = \"casual_selfie\"\n    if not visual.framing:\n        visual.framing = \"natural_medium_or_medium_wide\"\n    if visual.face_visible is None and not visual.face_hidden and not visual.back_to_camera:\n        visual.face_visible = True\n\n    latest_partner_turn = next(\n        (turn for turn in reversed(context.recent_conversation or []) if str(turn.role).lower() in {\"assistant\", \"partner\", \"bot\"} and turn.text_summary),\n        None,\n    )\n    if latest_partner_turn and not visual.scene_explicit_current_request:\n        visual.current_scene_from_chat = True\n        visual.scene_context_summary = str(latest_partner_turn.text_summary)[:280]\n        scene_constraint = \"Keep the photo in the partner's latest stated current location and activity from the conversation: \" + visual.scene_context_summary\n        if scene_constraint not in visual.freeform_visual_constraints:\n            visual.freeform_visual_constraints.append(scene_constraint)\n\n    for constraint in (\n        \"believable handheld phone capture\",\n        \"same persistent partner identity as every previous photo\",\n        \"not a staged third-person portrait unless explicitly requested\",\n    ):\n        if constraint not in visual.realism_constraints:\n            visual.realism_constraints.append(constraint)\n    logger.info(\n        \"IMAGE_PARTNER_PHOTO_DEFAULTS_APPLIED action=%s camera_mode=%s framing=%s current_scene_from_chat=%s\",\n        decision.action, visual.camera_mode, visual.framing, visual.current_scene_from_chat,\n    )\n    return decision\n'''
if 'def enforce_partner_photo_defaults(' not in text:
    text = text.replace(marker, helper + marker, 1)
text = text.replace(
    '"Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. "',
    '"Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. For a generic request to see the partner now, default to a believable casual handheld selfie; use mirror_selfie for full-body unless the user explicitly requests timer/tripod or another camera method. "',
)
text = text.replace(
    '"Extract scene/location/environment_type/privacy and mark scene_explicit_current_request=true when the current message names them. Extract pose, activity, wardrobe, framing, gaze, visible_objects, held_objects, required and forbidden body regions, and freeform constraints. Preserve explicit current instructions over routine context. A private location alone is not adult intent. "',
    '"Extract scene/location/environment_type/privacy and mark scene_explicit_current_request=true when the current message names them. For requests meaning now/currently/from where you are, treat the most recent assistant statement about the partner current location, support surface and activity as authoritative current-world context; populate scene/location/activity, set current_scene_from_chat=true, and summarize it in scene_context_summary. Do not silently replace that current scene with a routine or generic home/street default. Extract pose, activity, wardrobe, framing, gaze, visible_objects, held_objects, required and forbidden body regions, and freeform constraints. Preserve explicit current instructions over conversation context, and conversation context over routine context. A private location alone is not adult intent. "',
)
path.write_text(text, encoding='utf-8')

# telegram wiring
path = Path('app/api/telegram.py')
text = path.read_text(encoding='utf-8')
text = replace_once(
    text,
    '    enforce_referenced_object_request, supersede_pending_image_clarification,\n',
    '    enforce_referenced_object_request, enforce_partner_photo_defaults, supersede_pending_image_clarification,\n',
    'telegram import',
)
text = replace_once(
    text,
    '        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)\n        semantic_decision = enforce_referenced_object_request(context, deterministic_action, semantic_decision)\n',
    '        semantic_decision = enforce_clear_image_request_action(deterministic_action, semantic_decision)\n        semantic_decision = enforce_partner_photo_defaults(context, semantic_decision)\n        semantic_decision = enforce_referenced_object_request(context, deterministic_action, semantic_decision)\n',
    'telegram policy order',
)
path.write_text(text, encoding='utf-8')

# partner contract
path = Path('app/services/partner_photo_contract.py')
text = path.read_text(encoding='utf-8')
text = replace_once(
    text,
    '    world_memory_context: list[str] = field(default_factory=list)\n',
    '    world_memory_context: list[str] = field(default_factory=list)\n    identity_consistency_required: bool = True\n    current_scene_from_chat: bool = False\n    scene_context_summary: str | None = None\n',
    'contract fields',
)
old = '''    if hands_only or object_only or pet_only:\n        camera = camera or "point_of_view"\n        framing = framing or "detail"\n    elif framing == "full_body" and camera in {None, "casual_selfie"}:\n        # A true arm-length full-body selfie is usually implausible. A mirror or timer is natural.\n        camera = "mirror_selfie"\n    else:\n        camera = camera or "casual_phone_photo"\n        framing = framing or "natural_medium_or_medium_wide"\n'''
new = '''    if hands_only or object_only or pet_only:\n        camera = camera or "point_of_view"\n        framing = framing or "detail"\n    elif back_to_camera:\n        camera = camera or "tripod_timer"\n        framing = framing or "natural_medium_or_medium_wide"\n    elif framing == "full_body" and camera in {None, "casual_selfie"}:\n        # A true arm-length full-body selfie is usually implausible. A mirror or timer is natural.\n        camera = "mirror_selfie"\n    else:\n        camera = camera or ("casual_selfie" if primary_subject == "partner" and partner_visible else "casual_phone_photo")\n        framing = framing or "natural_medium_or_medium_wide"\n    if primary_subject == "partner" and partner_visible and camera in {"casual_selfie", "mirror_selfie"} and face_visible is None and not face_hidden:\n        face_visible = True\n'''
text = replace_once(text, old, new, 'selfie-first contract')
text = replace_once(
    text,
    '        expected_human_subject_count=expected_humans,\n',
    '        expected_human_subject_count=expected_humans,\n        identity_consistency_required=bool(_value(visual_intent, "identity_continuity_required", True) and partner_visible and identity_scope != "absent"),\n        current_scene_from_chat=bool(_value(visual_intent, "current_scene_from_chat", False)),\n        scene_context_summary=str(_value(visual_intent, "scene_context_summary", "") or "").strip() or None,\n',
    'contract return continuity',
)
text = replace_once(
    text,
    '    if contract.get("world_memory_context"):\n        lines.append("Relevant established partner-world memory, use only when applicable and never invent conflicting details: " + " | ".join(contract["world_memory_context"]) + ".")\n',
    '    if contract.get("current_scene_from_chat") and contract.get("scene_context_summary"):\n        lines.append("Current-moment continuity is mandatory: keep the visible setting, support surface and activity consistent with the latest stated partner context: " + str(contract["scene_context_summary"]) + ".")\n    if contract.get("identity_consistency_required"):\n        lines.append("Identity continuity is mandatory: this must be the same recurring fictional partner, never a new generic person.")\n    if contract.get("world_memory_context"):\n        lines.append("Relevant established partner-world memory, use only when applicable and never invent conflicting details: " + " | ".join(contract["world_memory_context"]) + ".")\n',
    'contract prompt continuity',
)
path.write_text(text, encoding='utf-8')

# image pipeline
path = Path('app/services/image_pipeline_v2.py')
text = path.read_text(encoding='utf-8')
text = text.replace("PROMPT_ENGINE_VERSION = 'image-prompt-v1.10.0'", "PROMPT_ENGINE_VERSION = 'image-prompt-v1.11.0'", 1)
text = text.replace("PLAN_VERSION = 'resolved-image-plan-v2.3'", "PLAN_VERSION = 'resolved-image-plan-v2.4'", 1)
text = replace_once(
    text,
    "    camera_mode=contract.get('camera_mode') or intent.composition.camera or 'casual_phone_photo'\n",
    "    camera_mode=contract.get('camera_mode') or intent.composition.camera or ('casual_selfie' if contract.get('partner_visible', True) and not contract.get('object_only') and not contract.get('pet_only') and not contract.get('hands_only') else 'casual_phone_photo')\n",
    'visual requirements selfie default',
)
text = replace_once(
    text,
    "    explicit_scene=bool(intent.scene.explicit_current_request and (intent.scene.scene_key or intent.scene.location))\n    vr.visibility_targets.environment_visible=bool(intent.scene.scene_key or intent.scene.location or intent.scene.support_surface)\n    vr.environment_visibility_required=bool(explicit_scene or intent.scene.support_surface)\n",
    "    explicit_scene=bool(intent.scene.explicit_current_request and (intent.scene.scene_key or intent.scene.location))\n    current_scene_required=bool(contract.get('current_scene_from_chat') and contract.get('scene_context_summary'))\n    vr.visibility_targets.environment_visible=bool(intent.scene.scene_key or intent.scene.location or intent.scene.support_surface or current_scene_required)\n    vr.environment_visibility_required=bool(explicit_scene or intent.scene.support_surface or current_scene_required)\n",
    'scene continuity requirement',
)
text = replace_once(
    text,
    "        'required_scene_elements': list(dict.fromkeys([x for x in [intent.scene.scene_key, intent.scene.location, *(intent.scene.required_visible_environment_elements or [])] if x])),\n",
    "        'required_scene_elements': list(dict.fromkeys([x for x in [intent.scene.scene_key, intent.scene.location, contract.get('scene_context_summary') if contract.get('current_scene_from_chat') else None, *(intent.scene.required_visible_environment_elements or [])] if x])),\n",
    'scene continuity must satisfy',
)
text = replace_once(
    text,
    "    visual_requirements=resolve_visual_requirements(intent, user_request=user_request, previous_job=source_job)\n    ap=normalize_anatomical_profile",
    "    visual_requirements=resolve_visual_requirements(intent, user_request=user_request, previous_job=source_job)\n    identity_anchor=identity_descriptor_v2(profile)\n    anchored_contract=dict(visual_requirements.photo_contract or {})\n    anchored_contract['identity_anchor']=identity_anchor\n    anchored_contract['identity_consistency_required']=bool(anchored_contract.get('identity_consistency_required', True) and visual_requirements.partner_visible and visual_requirements.identity_visibility_scope != 'absent')\n    visual_requirements.photo_contract=anchored_contract\n    visual_requirements.must_satisfy['identity_anchor']=identity_anchor\n    ap=normalize_anatomical_profile",
    'identity anchor',
)
path.write_text(text, encoding='utf-8')

# QA
path = Path('app/services/generated_image_qa_service.py')
text = path.read_text(encoding='utf-8')
text = text.replace("'hands_only_mismatch'", "'hands_only_mismatch','selfie_required'", 1)
text = text.replace(
    'Check the requested primary subject, required objects or pet, partner visibility, face shown or hidden, back-facing pose, framing, scene, camera method, and whether the capture is physically plausible.',
    'Check the requested primary subject, required objects or pet, partner visibility, face shown or hidden, back-facing pose, framing, scene, camera method, and whether the capture is physically plausible. When identity_anchor is supplied, compare every visible identity cue against it and set identity_consistency_reasonable=false on any meaningful face, hair, skin-tone, age-appearance, gender-presentation or body-build drift. A casual_selfie must visibly have believable handheld selfie perspective; a staged third-person portrait is not a selfie.',
    1,
)
text = replace_once(
    text,
    "        'required_visible_objects': vr.get('required_objects') or must.get('required_visible_objects') or [],\n",
    "        'required_visible_objects': vr.get('required_objects') or must.get('required_visible_objects') or [],\n        'identity_anchor': (vr.get('photo_contract') or {}).get('identity_anchor') or must.get('identity_anchor'),\n        'identity_consistency_required': bool((vr.get('photo_contract') or {}).get('identity_consistency_required')),\n        'current_scene_from_chat': bool((vr.get('photo_contract') or {}).get('current_scene_from_chat')),\n        'scene_context_summary': (vr.get('photo_contract') or {}).get('scene_context_summary'),\n",
    'qa payload continuity',
)
text = replace_once(
    text,
    "        if contract.get('camera_mode') and camera_matches is not True: codes.append('camera_mode_mismatch')\n        if contract.get('natural_capture_required', True) and (natural_capture is not True or looks_like_id): codes.append('id_photo_regression' if looks_like_id else 'implausible_camera_capture')\n",
    "        if contract.get('camera_mode') and camera_matches is not True: codes.append('camera_mode_mismatch')\n        if contract.get('camera_mode') == 'casual_selfie' and selfie is not True: codes.extend(['camera_mode_mismatch','selfie_required'])\n        if contract.get('camera_mode') == 'mirror_selfie' and mirror_selfie is not True: codes.extend(['camera_mode_mismatch','selfie_required'])\n        if contract.get('current_scene_from_chat') and requested_scene_visible is not True: codes.extend(['requested_scene_not_visible','wrong_scene'])\n        if contract.get('identity_consistency_required') and contract.get('identity_visibility_scope') != 'absent' and identity_ok is not True: codes.append('identity_inconsistent')\n        if contract.get('natural_capture_required', True) and (natural_capture is not True or looks_like_id): codes.append('id_photo_regression' if looks_like_id else 'implausible_camera_capture')\n",
    'qa fail closed continuity',
)
text = text.replace("    if identity_ok is False and contract.get('identity_visibility_scope') != 'absent': codes.append('identity_inconsistent')\n", "", 1)
text = replace_once(
    text,
    "        if contract.get('camera_mode') and qa.get('camera_mode_matches_request') is not True: return False\n",
    "        if contract.get('camera_mode') and qa.get('camera_mode_matches_request') is not True: return False\n        if contract.get('camera_mode') == 'casual_selfie' and qa.get('selfie_detected') is not True: return False\n        if contract.get('camera_mode') == 'mirror_selfie' and qa.get('mirror_selfie_detected') is not True: return False\n        if contract.get('current_scene_from_chat') and qa.get('requested_scene_visible') is not True: return False\n        if contract.get('identity_consistency_required') and contract.get('identity_visibility_scope') != 'absent' and qa.get('identity_consistency_reasonable') is not True: return False\n",
    'metadata continuity gate',
)
path.write_text(text, encoding='utf-8')

# metadata selfie flags
path = Path('app/services/image_generation_service.py')
text = path.read_text(encoding='utf-8')
old = "'selfie_allowed':'one_person_selfie' in ((compiled.sections or {}).get('camera_mode') or '') or 'one_person_mirror_selfie' in ((compiled.sections or {}).get('camera_mode') or ''),'mirror_allowed':'one_person_mirror_selfie' in ((compiled.sections or {}).get('camera_mode') or ''),"
new = "'selfie_allowed':plan.visual_requirements.camera_mode in {'casual_selfie','mirror_selfie'},'mirror_allowed':plan.visual_requirements.camera_mode == 'mirror_selfie',"
text = replace_once(text, old, new, 'selfie metadata flags')
path.write_text(text, encoding='utf-8')

# tests
Path('tests/test_partner_selfie_continuity.py').write_text('''from app.services.semantic_image_intent_router import (\n    ConversationTurnSummary, SemanticImageAction, SemanticImageDecision,\n    SemanticImageRouterContext, VisualIntent, enforce_partner_photo_defaults,\n)\nfrom app.services.partner_photo_contract import build_partner_photo_contract\nfrom app.services.generated_image_qa_service import evaluate_generated_image_composition_payload\n\n\ndef _context():\n    return SemanticImageRouterContext(\n        current_user_message="یه عکس بده از الانت",\n        recent_conversation=[\n            ConversationTurnSummary(role="assistant", text_summary="تازه رسیدم خونه و روی مبل نشستم")\n        ],\n    )\n\n\ndef test_generic_partner_photo_is_selfie_first_and_uses_current_scene_context():\n    decision = SemanticImageDecision(\n        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,\n        confidence=0.95, reason_code="direct_photo", visual_intent=VisualIntent(),\n    )\n    fixed = enforce_partner_photo_defaults(_context(), decision)\n    assert fixed.visual_intent.camera_mode == "casual_selfie"\n    assert fixed.visual_intent.face_visible is True\n    assert fixed.visual_intent.current_scene_from_chat is True\n    assert "روی مبل" in fixed.visual_intent.scene_context_summary\n    contract = build_partner_photo_contract(fixed.visual_intent)\n    assert contract["camera_mode"] == "casual_selfie"\n    assert contract["identity_consistency_required"] is True\n    assert contract["current_scene_from_chat"] is True\n\n\ndef test_full_body_partner_photo_defaults_to_mirror_selfie():\n    vi = VisualIntent(primary_subject="partner", framing="full_body")\n    decision = SemanticImageDecision(\n        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,\n        confidence=0.95, reason_code="direct_photo", visual_intent=vi,\n    )\n    fixed = enforce_partner_photo_defaults(_context(), decision)\n    assert fixed.visual_intent.camera_mode == "mirror_selfie"\n\n\ndef test_object_photo_is_not_forced_into_selfie():\n    vi = VisualIntent(primary_subject="object", object_only=True, partner_visible=False)\n    decision = SemanticImageDecision(\n        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,\n        confidence=0.95, reason_code="object_photo", visual_intent=vi,\n    )\n    fixed = enforce_partner_photo_defaults(_context(), decision)\n    assert fixed.visual_intent.camera_mode is None\n\n\ndef _qa_payload(**overrides):\n    payload = {\n        "person_count": 1, "face_count": 1, "intended_subject_count": 1,\n        "unexpected_additional_person_visible": False, "background_extra_person_visible": False,\n        "duplicate_subject_visible": False, "reflection_visible": False,\n        "selfie_detected": True, "mirror_selfie_detected": False,\n        "confidence": "high", "framing": "medium", "framing_matches_request": True,\n        "requested_scene_visible": True, "identity_consistency_reasonable": True,\n        "primary_subject_matches_request": True, "partner_visible": True, "face_visible": True,\n        "camera_mode_matches_request": True, "natural_capture_plausible": True,\n        "looks_like_id_photo": False, "reason_codes": [],\n    }\n    payload.update(overrides)\n    return payload\n\n\ndef _requirements():\n    contract = {\n        "primary_subject": "partner", "partner_visible": True, "camera_mode": "casual_selfie",\n        "natural_capture_required": True, "identity_visibility_scope": "full",\n        "identity_consistency_required": True, "identity_anchor": {"gender_presentation": "feminine", "hair": "dark wavy hair"},\n        "current_scene_from_chat": True, "scene_context_summary": "at home on the sofa",\n    }\n    return {\n        "requested_action": "new_generation", "environment_visibility_required": True,\n        "framing_requirement": "natural_medium_or_medium_wide", "photo_contract": contract,\n        "must_satisfy": {"required_scene_elements": ["at home on the sofa"]},\n    }\n\n\ndef test_staged_third_person_portrait_fails_selfie_requirement():\n    result = evaluate_generated_image_composition_payload(\n        _qa_payload(selfie_detected=False, camera_mode_matches_request=False),\n        expected_subject_count=1, selfie_allowed=True, visual_requirements=_requirements(),\n    )\n    assert result.passed is False\n    assert "selfie_required" in result.reason_codes\n\n\ndef test_identity_and_current_scene_are_fail_closed():\n    result = evaluate_generated_image_composition_payload(\n        _qa_payload(identity_consistency_reasonable=None, requested_scene_visible=False),\n        expected_subject_count=1, selfie_allowed=True, visual_requirements=_requirements(),\n    )\n    assert result.passed is False\n    assert "identity_inconsistent" in result.reason_codes\n    assert "wrong_scene" in result.reason_codes\n''', encoding='utf-8')
