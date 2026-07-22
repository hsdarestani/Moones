from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"{label}: target not found")
    return text.replace(old, new, 1)


# 1) Preserve semantic current-scene summaries instead of clearing them.
path = Path("app/services/semantic_image_intent_router.py")
text = path.read_text(encoding="utf-8")
old = '''    contextual_scene_parts = [
        str(value).strip() for value in (
            visual.scene, visual.location, visual.environment_type, visual.activity,
            *(visual.required_visible_environment_elements or []),
        ) if value not in (None, "")
    ]
    if contextual_scene_parts and not visual.scene_explicit_current_request:
        visual.current_scene_from_chat = True
        if not visual.scene_context_summary:
            visual.scene_context_summary = "; ".join(dict.fromkeys(contextual_scene_parts))[:280]
        scene_constraint = "Keep the photo in the partner's semantically resolved current location and activity from the conversation: " + visual.scene_context_summary
        if scene_constraint not in visual.freeform_visual_constraints:
            visual.freeform_visual_constraints.append(scene_constraint)
    elif not visual.scene_explicit_current_request:
        visual.current_scene_from_chat = False
        visual.scene_context_summary = None
'''
new = '''    contextual_scene_parts = [
        str(value).strip() for value in (
            visual.scene, visual.location, visual.environment_type, visual.activity,
            *(visual.required_visible_environment_elements or []),
        ) if value not in (None, "")
    ]
    semantic_scene_summary = str(visual.scene_context_summary or "").strip()
    semantic_scene_resolved = bool(visual.current_scene_from_chat and semantic_scene_summary)
    if not visual.scene_explicit_current_request:
        if semantic_scene_resolved:
            visual.current_scene_from_chat = True
            visual.scene_context_summary = semantic_scene_summary[:280]
        elif contextual_scene_parts:
            visual.current_scene_from_chat = True
            visual.scene_context_summary = "; ".join(dict.fromkeys(contextual_scene_parts))[:280]
        else:
            visual.current_scene_from_chat = False
            visual.scene_context_summary = None
        if visual.current_scene_from_chat and visual.scene_context_summary:
            scene_constraint = "Keep the photo in the partner's semantically resolved current location and activity from the conversation: " + visual.scene_context_summary
            if scene_constraint not in visual.freeform_visual_constraints:
                visual.freeform_visual_constraints.append(scene_constraint)
'''
text = replace_once(text, old, new, "preserve semantic scene summary")
old_prompt = "For requests meaning now/currently/from where you are, treat the most recent assistant statement about the partner current location, support surface and activity as authoritative current-world context; populate scene/location/activity, set current_scene_from_chat=true, and summarize it in scene_context_summary. Do not silently replace that current scene with a routine or generic home/street default."
new_prompt = "For requests meaning now/currently/from where you are, treat the most recent assistant statement about the partner current location, support surface and activity as authoritative current-world context. Always set current_scene_from_chat=true and provide a compact scene_context_summary when that statement contains current-world information, even when you cannot confidently canonicalize every scene/location/activity field. Do not silently replace that current scene with a routine or generic home/street default."
text = replace_once(text, old_prompt, new_prompt, "semantic router current-scene instruction")
path.write_text(text, encoding="utf-8")


# 2) Prevent routine context from overriding a semantically resolved conversation scene,
# and make the scene summary a first-class environment requirement in V2.
path = Path("app/services/image_generation_service.py")
text = path.read_text(encoding="utf-8")
anchor = '''def _build_request_context(db: Session, user: User, user_request: str):
'''
helper = '''def suppress_routine_scene_for_current_chat_scene(routine_slot, photo_contract):
    contract = dict(photo_contract or {})
    if not (contract.get('current_scene_from_chat') and str(contract.get('scene_context_summary') or '').strip()):
        return routine_slot
    cleaned = dict(routine_slot or {})
    cleaned['location'] = None
    cleaned['scene'] = None
    cleaned['environment_type'] = None
    return cleaned


'''
if helper not in text:
    if anchor not in text:
        raise RuntimeError("routine scene helper anchor not found")
    text = text.replace(anchor, helper + anchor, 1)
old = '''    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)
    intent.photo_contract=attach_world_memory_context(getattr(intent, 'photo_contract', {}), relevant_memories)
'''
new = '''    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)
    intent.photo_contract=attach_world_memory_context(getattr(intent, 'photo_contract', {}), relevant_memories)
    original_routine_location=(routine_slot or {}).get('location')
    routine_slot=suppress_routine_scene_for_current_chat_scene(routine_slot, intent.photo_contract)
    if original_routine_location and not (routine_slot or {}).get('location') and (intent.photo_contract or {}).get('current_scene_from_chat'):
        logger.info('IMAGE_CONVERSATION_SCENE_OVERRIDES_ROUTINE user_id=%s routine_location=%s scene_context_summary=%s', user.id, original_routine_location, (intent.photo_contract or {}).get('scene_context_summary'))
'''
text = replace_once(text, old, new, "suppress routine scene")
old = '''    if getattr(vi, 'required_visible_environment_elements', None): intent.scene.required_visible_environment_elements=list(vi.required_visible_environment_elements or [])
    if intent.scene.explicit_current_request:
'''
new = '''    if getattr(vi, 'required_visible_environment_elements', None): intent.scene.required_visible_environment_elements=list(vi.required_visible_environment_elements or [])
    if contract.get('current_scene_from_chat') and contract.get('scene_context_summary'):
        summary=str(contract.get('scene_context_summary')).strip()
        intent.scene.required_visible_environment_elements=list(dict.fromkeys([*(intent.scene.required_visible_environment_elements or []), summary]))
        free.append('current scene and activity: ' + summary)
        logger.info('IMAGE_SEMANTIC_CURRENT_SCENE_ATTACHED action=%s scene_context_summary=%s', getattr(semantic_decision, 'action', None), summary)
    if intent.scene.explicit_current_request:
'''
text = replace_once(text, old, new, "attach current scene summary")
path.write_text(text, encoding="utf-8")


# 3) Strengthen the prompt-level camera contract.
path = Path("app/services/partner_photo_contract.py")
text = path.read_text(encoding="utf-8")
old = '        "casual_selfie": "Camera logic: a believable casual phone selfie with natural arm-length perspective and visible environmental context, not a biometric headshot.",\n'
new = '        "casual_selfie": "Camera logic: the image viewpoint originates from the phone lens held naturally at arm length. The phone device itself is outside the frame in a non-mirror selfie; no external photographer, overhead camera, or third-person viewpoint. Keep visible environmental context and avoid a biometric headshot.",\n'
text = replace_once(text, old, new, "casual selfie camera contract")
old = '        "mirror_selfie": "Camera logic: a believable mirror selfie; the phone and mirror geometry must be plausible and any reflection must be the same subject.",\n'
new = '        "mirror_selfie": "Camera logic: a believable mirror selfie photographed through the mirror. The phone may be visible only as part of the same-subject mirror geometry; no external or overhead third-person camera and any reflection must be the same subject.",\n'
text = replace_once(text, old, new, "mirror selfie camera contract")
path.write_text(text, encoding="utf-8")


# 4) Add explicit camera-source geometry to fail-closed QA and delivery gating.
path = Path("app/services/generated_image_qa_service.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "'hands_only_mismatch','selfie_required'\n",
    "'hands_only_mismatch','selfie_required','selfie_geometry_inconsistent','third_person_viewpoint','visible_phone_in_non_mirror_selfie'\n",
    "QA reason codes",
)
text = replace_once(
    text,
    "    hands_only_matches_request: bool | None = None\n",
    "    hands_only_matches_request: bool | None = None\n    camera_source_geometry_consistent: bool | None = None\n    selfie_lens_perspective_plausible: bool | None = None\n    third_person_viewpoint_detected: bool = False\n    visible_held_phone_detected: bool = False\n",
    "QA geometry result fields",
)
old_sentence = "A casual_selfie must visibly have believable handheld selfie perspective; a staged third-person portrait is not a selfie."
new_sentence = "A casual_selfie must visibly originate from the held phone lens at natural arm length. In a non-mirror selfie the phone device itself must not be visible; a visible held phone combined with an external or overhead viewpoint is third-person staging, not a selfie. Set camera_source_geometry_consistent, selfie_lens_perspective_plausible, third_person_viewpoint_detected, and visible_held_phone_detected explicitly."
text = replace_once(text, old_sentence, new_sentence, "QA geometry instruction")
old_schema = '"camera_mode_detected":"casual_phone_photo","camera_mode_matches_request":true,"natural_capture_plausible":true,"looks_like_id_photo":false,"hands_only_matches_request":true,"reason_codes":[]}'
new_schema = '"camera_mode_detected":"casual_phone_photo","camera_mode_matches_request":true,"camera_source_geometry_consistent":true,"selfie_lens_perspective_plausible":true,"third_person_viewpoint_detected":false,"visible_held_phone_detected":false,"natural_capture_plausible":true,"looks_like_id_photo":false,"hands_only_matches_request":true,"reason_codes":[]}'
text = replace_once(text, old_schema, new_schema, "QA geometry schema")
old = '''    hands_only_matches=None if payload.get('hands_only_matches_request') is None else _bool(payload.get('hands_only_matches_request'))
    if contract:
'''
new = '''    hands_only_matches=None if payload.get('hands_only_matches_request') is None else _bool(payload.get('hands_only_matches_request'))
    camera_geometry=None if payload.get('camera_source_geometry_consistent') is None else _bool(payload.get('camera_source_geometry_consistent'))
    selfie_lens=None if payload.get('selfie_lens_perspective_plausible') is None else _bool(payload.get('selfie_lens_perspective_plausible'))
    third_person=_bool(payload.get('third_person_viewpoint_detected'))
    visible_held_phone=_bool(payload.get('visible_held_phone_detected'))
    if contract:
'''
text = replace_once(text, old, new, "parse QA camera geometry")
old = '''        if contract.get('camera_mode') == 'casual_selfie' and selfie is not True: codes.extend(['camera_mode_mismatch','selfie_required'])
        if contract.get('camera_mode') == 'mirror_selfie' and mirror_selfie is not True: codes.extend(['camera_mode_mismatch','selfie_required'])
'''
new = '''        if contract.get('camera_mode') == 'casual_selfie':
            if selfie is not True: codes.extend(['camera_mode_mismatch','selfie_required'])
            if camera_geometry is not True or selfie_lens is not True: codes.append('selfie_geometry_inconsistent')
            if third_person: codes.append('third_person_viewpoint')
            if visible_held_phone: codes.append('visible_phone_in_non_mirror_selfie')
        if contract.get('camera_mode') == 'mirror_selfie':
            if mirror_selfie is not True: codes.extend(['camera_mode_mismatch','selfie_required'])
            if camera_geometry is not True or third_person: codes.append('selfie_geometry_inconsistent')
'''
text = replace_once(text, old, new, "enforce QA camera geometry")
old = '''    result.hands_only_matches_request=hands_only_matches
    if requested_full_body and not result.passed:
'''
new = '''    result.hands_only_matches_request=hands_only_matches
    result.camera_source_geometry_consistent=camera_geometry
    result.selfie_lens_perspective_plausible=selfie_lens
    result.third_person_viewpoint_detected=third_person
    result.visible_held_phone_detected=visible_held_phone
    if requested_full_body and not result.passed:
'''
text = replace_once(text, old, new, "persist QA camera geometry")
old = '''        if contract.get('camera_mode') == 'casual_selfie' and qa.get('selfie_detected') is not True: return False
        if contract.get('camera_mode') == 'mirror_selfie' and qa.get('mirror_selfie_detected') is not True: return False
'''
new = '''        if contract.get('camera_mode') == 'casual_selfie':
            if qa.get('selfie_detected') is not True: return False
            if qa.get('camera_source_geometry_consistent') is not True: return False
            if qa.get('selfie_lens_perspective_plausible') is not True: return False
            if qa.get('third_person_viewpoint_detected') is not False: return False
            if qa.get('visible_held_phone_detected') is not False: return False
        if contract.get('camera_mode') == 'mirror_selfie':
            if qa.get('mirror_selfie_detected') is not True: return False
            if qa.get('camera_source_geometry_consistent') is not True: return False
            if qa.get('third_person_viewpoint_detected') is not False: return False
'''
text = replace_once(text, old, new, "delivery gate camera geometry")
old = "    elif codes & {'primary_subject_mismatch','requested_pet_missing','required_object_missing','unexpected_visible_partner','face_should_be_hidden','face_should_be_visible','back_view_mismatch','camera_mode_mismatch','implausible_camera_capture','id_photo_regression','hands_only_mismatch'}:\n"
new = "    elif codes & {'primary_subject_mismatch','requested_pet_missing','required_object_missing','unexpected_visible_partner','face_should_be_hidden','face_should_be_visible','back_view_mismatch','camera_mode_mismatch','implausible_camera_capture','id_photo_regression','hands_only_mismatch','selfie_required','selfie_geometry_inconsistent','third_person_viewpoint','visible_phone_in_non_mirror_selfie'}:\n"
text = replace_once(text, old, new, "QA user message geometry reasons")
old = "    if codes & {'camera_mode_mismatch','implausible_camera_capture','id_photo_regression'}:\n        lines.append('Use the requested physically plausible phone/mirror/tripod/POV camera method. Make it a spontaneous personal photo, never a passport, ID, casting, or studio headshot.')\n"
new = "    if codes & {'camera_mode_mismatch','implausible_camera_capture','id_photo_regression','selfie_required','selfie_geometry_inconsistent','third_person_viewpoint','visible_phone_in_non_mirror_selfie'}:\n        lines.append('Use the requested physically plausible phone/mirror/tripod/POV camera method. Make it a spontaneous personal photo, never a passport, ID, casting, or studio headshot.')\n        if contract.get('camera_mode') == 'casual_selfie':\n            lines.append('The viewpoint must come from the phone lens held at natural arm length. Keep the phone device outside the frame; no overhead camera, external photographer, or third-person viewpoint.')\n        elif contract.get('camera_mode') == 'mirror_selfie':\n            lines.append('Use coherent mirror geometry: the phone may appear only in the same-subject reflection, with no external or overhead third-person camera.')\n"
text = replace_once(text, old, new, "corrective prompt camera geometry")
path.write_text(text, encoding="utf-8")


# 5) Put the camera geometry contract into the provider prompt and negative prompt.
path = Path("app/services/image_pipeline_v2.py")
text = path.read_text(encoding="utf-8")
text = text.replace("PROMPT_ENGINE_VERSION = 'image-prompt-v1.11.0'", "PROMPT_ENGINE_VERSION = 'image-prompt-v1.12.0'", 1)
text = text.replace("PLAN_VERSION = 'resolved-image-plan-v2.4'", "PLAN_VERSION = 'resolved-image-plan-v2.5'", 1)
old = '''    if vr.face_hidden_required:
        neg_terms.extend(['visible face','recognizable face','reflected face','accidental headshot'])
    return CompiledImagePrompt(positive, ', '.join(dict.fromkeys(neg_terms)), {'width':plan.composition['width'],'height':plan.composition['height'],'seed':plan.seed_strategy.get('final_provider_seed')}, sec)
'''
new = '''    if vr.face_hidden_required:
        neg_terms.extend(['visible face','recognizable face','reflected face','accidental headshot'])
    if vr.camera_mode == 'casual_selfie':
        neg_terms.extend(['visible phone held by subject','phone device inside frame','external photographer viewpoint','third-person overhead view','camera above the subject','detached selfie camera'])
    elif vr.camera_mode == 'mirror_selfie':
        neg_terms.extend(['external photographer viewpoint','third-person overhead view','camera outside mirror geometry'])
    return CompiledImagePrompt(positive, ', '.join(dict.fromkeys(neg_terms)), {'width':plan.composition['width'],'height':plan.composition['height'],'seed':plan.seed_strategy.get('final_provider_seed')}, sec)
'''
text = replace_once(text, old, new, "provider negative camera geometry")
path.write_text(text, encoding="utf-8")


# 6) Add exact regression tests for both screenshots.
path = Path("tests/test_partner_selfie_continuity.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload\n",
    "from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload\nfrom app.services.image_generation_service import suppress_routine_scene_for_current_chat_scene\nfrom app.services.partner_photo_contract import prompt_constraints\n",
    "test imports",
)
old = '''        "camera_mode_matches_request": True, "natural_capture_plausible": True,
        "looks_like_id_photo": False, "reason_codes": [],
'''
new = '''        "camera_mode_matches_request": True, "camera_source_geometry_consistent": True,
        "selfie_lens_perspective_plausible": True, "third_person_viewpoint_detected": False,
        "visible_held_phone_detected": False, "natural_capture_plausible": True,
        "looks_like_id_photo": False, "reason_codes": [],
'''
text = replace_once(text, old, new, "test QA geometry defaults")
append = '''


def test_model_provided_current_scene_summary_survives_without_canonical_fields():
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,
        confidence=0.95, reason_code="direct_photo",
        visual_intent=VisualIntent(
            current_scene_from_chat=True,
            scene_context_summary="at home, lying on the sofa",
        ),
    )
    fixed = enforce_partner_photo_defaults(_context(), decision)
    assert fixed.visual_intent.current_scene_from_chat is True
    assert fixed.visual_intent.scene_context_summary == "at home, lying on the sofa"
    assert any("lying on the sofa" in item for item in fixed.visual_intent.freeform_visual_constraints)


def test_conversation_scene_removes_conflicting_routine_location():
    routine = {"location": "outdoor street", "slot_name": "afternoon", "environment_type": "public_outdoor"}
    contract = {"current_scene_from_chat": True, "scene_context_summary": "at home, lying on the sofa"}
    cleaned = suppress_routine_scene_for_current_chat_scene(routine, contract)
    assert cleaned["location"] is None
    assert cleaned["environment_type"] is None
    assert cleaned["slot_name"] == "afternoon"
    assert routine["location"] == "outdoor street"


def test_non_mirror_selfie_with_visible_phone_and_external_viewpoint_is_rejected():
    result = evaluate_generated_image_composition_payload(
        _qa_payload(
            selfie_detected=True,
            camera_mode_matches_request=True,
            camera_source_geometry_consistent=False,
            selfie_lens_perspective_plausible=False,
            third_person_viewpoint_detected=True,
            visible_held_phone_detected=True,
        ),
        expected_subject_count=1,
        selfie_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is False
    assert "selfie_geometry_inconsistent" in result.reason_codes
    assert "third_person_viewpoint" in result.reason_codes
    assert "visible_phone_in_non_mirror_selfie" in result.reason_codes


def test_valid_arm_length_selfie_geometry_passes():
    result = evaluate_generated_image_composition_payload(
        _qa_payload(),
        expected_subject_count=1,
        selfie_allowed=True,
        visual_requirements=_requirements(),
    )
    assert result.passed is True
    assert result.camera_source_geometry_consistent is True
    assert result.third_person_viewpoint_detected is False
    assert result.visible_held_phone_detected is False


def test_casual_selfie_prompt_forbids_external_camera_and_visible_phone():
    lines = prompt_constraints({"camera_mode": "casual_selfie", "natural_capture_required": True})
    joined = " ".join(lines)
    assert "phone device itself is outside the frame" in joined
    assert "no external photographer" in joined
    assert "no external photographer" in joined
'''
if "test_model_provided_current_scene_summary_survives_without_canonical_fields" not in text:
    text += append
path.write_text(text, encoding="utf-8")
