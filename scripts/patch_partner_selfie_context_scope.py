from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f'{label} target not found')
    return text.replace(old, new, 1)

path=Path('app/services/semantic_image_intent_router.py')
text=path.read_text(encoding='utf-8')
text=replace_once(
    text,
    '    camera_mode: str | None = None\n    required_body_regions: list[str] = field(default_factory=list)\n',
    '    camera_mode: str | None = None\n    camera_explicit_current_request: bool = False\n    framing_explicit_current_request: bool = False\n    required_body_regions: list[str] = field(default_factory=list)\n',
    'explicit camera fields',
)
old='''    if not visual.camera_mode:\n        if visual.back_to_camera:\n            visual.camera_mode = "tripod_timer"\n        elif visual.framing == "full_body":\n            visual.camera_mode = "mirror_selfie"\n        else:\n            visual.camera_mode = "casual_selfie"\n    if not visual.framing:\n        visual.framing = "natural_medium_or_medium_wide"\n    if visual.face_visible is None and not visual.face_hidden and not visual.back_to_camera:\n        visual.face_visible = True\n\n    latest_partner_turn = next(\n        (turn for turn in reversed(context.recent_conversation or []) if str(turn.role).lower() in {"assistant", "partner", "bot"} and turn.text_summary),\n        None,\n    )\n    if latest_partner_turn and not visual.scene_explicit_current_request:\n        visual.current_scene_from_chat = True\n        visual.scene_context_summary = str(latest_partner_turn.text_summary)[:280]\n        scene_constraint = "Keep the photo in the partner's latest stated current location and activity from the conversation: " + visual.scene_context_summary\n        if scene_constraint not in visual.freeform_visual_constraints:\n            visual.freeform_visual_constraints.append(scene_constraint)\n'''
new='''    if not visual.camera_explicit_current_request:\n        if visual.back_to_camera:\n            visual.camera_mode = "tripod_timer"\n        elif visual.framing == "full_body":\n            visual.camera_mode = "mirror_selfie"\n        else:\n            visual.camera_mode = "casual_selfie"\n    if not visual.framing_explicit_current_request and not visual.framing:\n        visual.framing = "natural_medium_or_medium_wide"\n    if visual.face_visible is None and not visual.face_hidden and not visual.back_to_camera:\n        visual.face_visible = True\n\n    contextual_scene_parts = [\n        str(value).strip() for value in (\n            visual.scene, visual.location, visual.environment_type, visual.activity,\n            *(visual.required_visible_environment_elements or []),\n        ) if value not in (None, "")\n    ]\n    if contextual_scene_parts and not visual.scene_explicit_current_request:\n        visual.current_scene_from_chat = True\n        if not visual.scene_context_summary:\n            visual.scene_context_summary = "; ".join(dict.fromkeys(contextual_scene_parts))[:280]\n        scene_constraint = "Keep the photo in the partner's semantically resolved current location and activity from the conversation: " + visual.scene_context_summary\n        if scene_constraint not in visual.freeform_visual_constraints:\n            visual.freeform_visual_constraints.append(scene_constraint)\n    elif not visual.scene_explicit_current_request:\n        visual.current_scene_from_chat = False\n        visual.scene_context_summary = None\n'''
text=replace_once(text,old,new,'context-scoped selfie defaults')
text=text.replace(
    '"Populate request_type and primary_subject as partner, pet, object, or scene. Set partner_visible, pet_visible, object_only, pet_only, hands_only, face_visible, face_hidden, and back_to_camera. Set camera_mode to casual_selfie, mirror_selfie, tripod_timer, point_of_view, passenger_pov, dashboard_mount, candid, or casual_phone_photo. A full-body selfie normally means mirror_selfie unless timer/tripod is explicit. Coffee, food, personal-object, and pet photos may omit the partner. Hands-only means hands_only=true, face_hidden=true, hands in required_body_regions, and point_of_view unless another camera method is explicit. Back-view means back_to_camera=true. "',
    '"Populate request_type and primary_subject as partner, pet, object, or scene. Set partner_visible, pet_visible, object_only, pet_only, hands_only, face_visible, face_hidden, and back_to_camera. Set camera_mode to casual_selfie, mirror_selfie, tripod_timer, point_of_view, passenger_pov, dashboard_mount, candid, or casual_phone_photo. Set camera_explicit_current_request=true only when the current user message explicitly requests the camera method; set framing_explicit_current_request=true only when the current user message explicitly requests framing. A full-body selfie normally means mirror_selfie unless timer/tripod is explicit. Coffee, food, personal-object, and pet photos may omit the partner. Hands-only means hands_only=true, face_hidden=true, hands in required_body_regions, and point_of_view unless another camera method is explicit. Back-view means back_to_camera=true. "',
    1,
)
path.write_text(text,encoding='utf-8')

test=Path('tests/test_partner_selfie_continuity.py')
t=test.read_text(encoding='utf-8')
old='''def _context():\n    return SemanticImageRouterContext(\n        current_user_message="یه عکس بده از الانت",\n        recent_conversation=[\n            ConversationTurnSummary(role="assistant", text_summary="تازه رسیدم خونه و روی مبل نشستم")\n        ],\n    )\n'''
new='''def _context():\n    return SemanticImageRouterContext(\n        current_user_message="یه عکس بده از الانت",\n        recent_conversation=[\n            ConversationTurnSummary(role="assistant", text_summary="تازه رسیدم خونه و روی مبل نشستم")\n        ],\n    )\n'''
# existing helper intentionally remains; semantic scene must be represented in VisualIntent.
if old not in t:
    raise RuntimeError('test context target not found')
t=t.replace(
    '        confidence=0.95, reason_code="direct_photo", visual_intent=VisualIntent(),\n',
    '        confidence=0.95, reason_code="direct_photo", visual_intent=VisualIntent(location="home", activity="sitting on the sofa"),\n',
    1,
)
t=t.replace('    assert "روی مبل" in fixed.visual_intent.scene_context_summary\n','    assert "sitting on the sofa" in fixed.visual_intent.scene_context_summary\n',1)
append='''\n\ndef test_non_scene_assistant_message_is_not_forced_into_photo_scene():\n    context = SemanticImageRouterContext(\n        current_user_message="یه عکس بده",\n        recent_conversation=[ConversationTurnSummary(role="assistant", text_summary="از بخش افزودنی‌ها می‌تونی قابلیت‌ها رو فعال کنی")],\n    )\n    decision = SemanticImageDecision(\n        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True,\n        confidence=0.95, reason_code="direct_photo", visual_intent=VisualIntent(),\n    )\n    fixed = enforce_partner_photo_defaults(context, decision)\n    assert fixed.visual_intent.current_scene_from_chat is False\n    assert fixed.visual_intent.scene_context_summary is None\n\n\ndef test_generic_model_camera_default_is_overridden_but_explicit_timer_is_preserved():\n    generic = SemanticImageDecision(\n        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True, confidence=0.95,\n        reason_code="direct_photo", visual_intent=VisualIntent(camera_mode="casual_phone_photo"),\n    )\n    assert enforce_partner_photo_defaults(_context(), generic).visual_intent.camera_mode == "casual_selfie"\n    explicit = SemanticImageDecision(\n        action=SemanticImageAction.GENERATE_NEW, media_delivery_requested=True, confidence=0.95,\n        reason_code="direct_photo", visual_intent=VisualIntent(camera_mode="tripod_timer", camera_explicit_current_request=True),\n    )\n    assert enforce_partner_photo_defaults(_context(), explicit).visual_intent.camera_mode == "tripod_timer"\n'''
if 'test_non_scene_assistant_message_is_not_forced_into_photo_scene' not in t:
    t += append
test.write_text(t,encoding='utf-8')
