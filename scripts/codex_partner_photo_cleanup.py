from __future__ import annotations

from pathlib import Path
import re


def sub_once(path: str, pattern: str, replacement: str) -> None:
    p=Path(path); text=p.read_text(encoding='utf-8')
    new,count=re.subn(pattern,replacement,text,count=1,flags=re.S|re.M)
    if count != 1:
        raise RuntimeError(f'expected one cleanup target in {path}, got {count}: {pattern[:120]}')
    p.write_text(new,encoding='utf-8')


def replace_once(path: str, old: str, new: str) -> None:
    p=Path(path); text=p.read_text(encoding='utf-8')
    if old not in text:
        if new in text: return
        raise RuntimeError(f'cleanup target missing in {path}: {old[:120]!r}')
    p.write_text(text.replace(old,new,1),encoding='utf-8')


# 1. Normalize VisualIntent and semantic model instructions after iterative patch runs.
visual_intent='''@dataclass
class VisualIntent:
    subject_focus: str | None = None
    body_or_face_regions: list[str] = field(default_factory=list)
    scene: str | None = None
    location: str | None = None
    environment_type: str | None = None
    privacy: str | None = None
    required_visible_environment_elements: list[str] = field(default_factory=list)
    scene_explicit_current_request: bool = False
    pose: str | None = None
    activity: str | None = None
    expression: str | None = None
    wardrobe: str | None = None
    visible_objects: list[str] = field(default_factory=list)
    held_objects: list[str] = field(default_factory=list)
    camera: str | None = None
    framing: str | None = None
    lighting: str | None = None
    exclusions: list[str] = field(default_factory=list)
    secondary_subject: str | None = None
    interaction: str | None = None
    expected_subject_count: int | None = None
    freeform_visual_constraints: list[str] = field(default_factory=list)
    confidence: float = 1.0
    gaze_direction: str | None = None
    eye_contact_required: bool = False
    nudity_level: str | None = None
    explicit_anatomy_focus: bool = False
    request_type: str | None = None
    primary_subject: str | None = None
    partner_visible: bool | None = None
    pet_visible: bool = False
    object_only: bool = False
    pet_only: bool = False
    hands_only: bool = False
    face_visible: bool | None = None
    face_hidden: bool = False
    back_to_camera: bool = False
    camera_mode: str | None = None
    required_body_regions: list[str] = field(default_factory=list)
    forbidden_body_regions: list[str] = field(default_factory=list)
    realism_constraints: list[str] = field(default_factory=list)
    natural_capture_required: bool = True


'''
sub_once('app/services/semantic_image_intent_router.py', r'@dataclass\nclass VisualIntent:.*?(?=@dataclass\nclass SemanticSourceReference:)', visual_intent)

system_prompt='''        system = (
            "Classify whether the user's current Persian message is chat or an image action. "
            "Actions: generate_new means a newly generated image; refine_previous changes a previous image; variation means another related image; resend_exact resends the exact prior artifact; status_query asks about an active/recent job; cancel_pending cancels it; chat discusses images without requesting delivery; clarify is only for genuine action/source ambiguity. "
            "Use current message, recent conversation, reply metadata, active/latest image job, and recent resolved plan. A direct answer to a prior clarification must resolve that clarification and must not create a loop. Short questions like چیشد or عکس کجاست are status_query when an image job is relevant. Confusion after an error is chat unless another image is explicitly requested. "
            "Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. "
            "Populate request_type and primary_subject as partner, pet, object, or scene. Set partner_visible, pet_visible, object_only, pet_only, hands_only, face_visible, face_hidden, and back_to_camera. Set camera_mode to casual_selfie, mirror_selfie, tripod_timer, point_of_view, passenger_pov, dashboard_mount, candid, or casual_phone_photo. A full-body selfie normally means mirror_selfie unless timer/tripod is explicit. Coffee, food, personal-object, and pet photos may omit the partner. Hands-only means hands_only=true, face_hidden=true, hands in required_body_regions, and point_of_view unless another camera method is explicit. Back-view means back_to_camera=true. "
            "Extract scene/location/environment_type/privacy and mark scene_explicit_current_request=true when the current message names them. Extract pose, activity, wardrobe, framing, gaze, visible_objects, held_objects, required and forbidden body regions, and freeform constraints. Preserve explicit current instructions over routine context. A private location alone is not adult intent. "
            "Set natural_capture_required=true unless studio/editorial imagery is explicitly requested. The result must behave like a plausible personal photo from a real partner: avoid ID/passport/casting defaults and impossible self-photography while driving. "
            "For adult visual requests set nudity_level to normal, suggestive, lingerie, topless, or full_nudity. Explicit genital/anatomy focus sets explicit_anatomy_focus=true, includes genitals in body_or_face_regions, and sets safety_relevant_signals.explicit_genital_visibility=true. Adult image access is checked elsewhere; do not add a confirmation flow here. "
            "Return only valid JSON matching the schema. Do not decide billing, entitlement, source ownership, provider execution, or delivery."
        )
'''
sub_once('app/services/semantic_image_intent_router.py', r'        system = \(.*?        \)\n        user_payload=', system_prompt + '        user_payload=')
replace_once(
    'app/services/semantic_image_intent_router.py',
    "    extracted = sorted(k for k, v in vi.items() if k != 'confidence' and v not in (None, False, \"\", [], {}))\n",
    "    extracted = sorted(k for k, v in vi.items() if k != 'confidence' and v not in (None, False, \"\", [], {}) and not (k == 'natural_capture_required' and v is True))\n",
)
replace_once(
    'app/services/semantic_image_intent_router.py',
    '''    # Detailed media requests must reach the semantic model so scene, camera,
    # subject visibility, pet/object focus, pose and adult intent are not discarded.
    # Only exact control/clarification commands above are handled deterministically.
    return None
''',
    '''    # Compatibility fallback only. Production still calls the semantic model for
    # GENERATE_NEW so this helper never becomes the source of an empty VisualIntent.
    wants_visual = "عکس" in t or "تصویر" in t or "ببینمت" in t or "نشونم بده" in t
    delivery = any(v in t for v in ["بده", "بدی", "بفرست", "بفرستی", "بساز", "درست کن", "ببینمت", "نشونم بده", "باشی"])
    if wants_visual and delivery:
        return SemanticImageAction.GENERATE_NEW
    return None
''',
)

# 2. Natural production routing: generate-new must still use the model for detail extraction.
replace_once(
    'app/api/telegram.py',
    '''        deterministic_action = pending_resolution.action if pending_resolution else canonical_explicit_image_action(text)
        if deterministic_action:
          semantic_decision = SemanticImageDecision(action=deterministic_action, media_delivery_requested=deterministic_action not in {SemanticImageAction.CHAT, SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}, confidence=1.0, reason_code='resolved_structured_image_intent')
        else:
          semantic_decision = await SemanticImageIntentRouter(VeniceSemanticImageIntentModel()).decide(context, shadow_or_evaluation=False)
''',
    '''        deterministic_action = pending_resolution.action if pending_resolution else canonical_explicit_image_action(text)
        deterministic_generate_requires_extraction = bool(not pending_resolution and deterministic_action == SemanticImageAction.GENERATE_NEW)
        if deterministic_action and not deterministic_generate_requires_extraction:
          semantic_decision = SemanticImageDecision(action=deterministic_action, media_delivery_requested=deterministic_action not in {SemanticImageAction.CHAT, SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}, confidence=1.0, reason_code='resolved_structured_image_intent')
        else:
          semantic_decision = await SemanticImageIntentRouter(VeniceSemanticImageIntentModel()).decide(context, shadow_or_evaluation=False)
''',
)
telegram=Path('app/api/telegram.py'); ttext=telegram.read_text(encoding='utf-8')
if 'def _log_image_v2_route_shadow_if_enabled' not in ttext:
    helper='''\ndef _log_image_v2_route_shadow_if_enabled(db: Session, *, text: str, source_message_id: int | None, legacy_route: str) -> bool:\n    image_v2_flags = resolve_image_pipeline_v2_flags(db)\n    if not image_v2_flags.shadow_enabled:\n        return False\n    try:\n        from app.services import image_pipeline_v2 as v2\n        route_shadow = v2.route_shadow_decision(text, source_message_id=source_message_id, legacy_route=legacy_route)\n        compact_keys = {'request_hash','source_message_id','legacy_route','v2_is_image_request','v2_detected_action','route_mismatch','fallback_required','policy_reason_code'}\n        compact_shadow = {k: route_shadow[k] for k in compact_keys if k in route_shadow}\n        logger.info("IMAGE_V2_ROUTE_SHADOW %s", json.dumps(compact_shadow, ensure_ascii=False, sort_keys=True))\n    except Exception as exc:\n        logger.info("IMAGE_V2_ROUTE_SHADOW_FAILED source_message_id=%s error=%s", source_message_id, type(exc).__name__)\n    return True\n\n'''
    ttext=ttext.replace('\ndef _semantic_decision_to_legacy_route', helper + '\ndef _semantic_decision_to_legacy_route', 1)
    telegram.write_text(ttext,encoding='utf-8')

# 3. Hands-only object photos still contain the partner's partial identity (their hands).
replace_once(
    'app/services/partner_photo_contract.py',
    '''    if object_only or primary_subject in {"object", "scene"}:
        primary_subject = "object" if primary_subject != "scene" else "scene"
        partner_visible = False
''',
    '''    if (object_only or primary_subject in {"object", "scene"}) and not hands_only:
        primary_subject = "object" if primary_subject != "scene" else "scene"
        partner_visible = False
''',
)

# 4. Clean prompt compiler internals and retain old invariant phrases.
replace_once(
    'app/services/image_pipeline_v2.py',
    "        subject_contract=f'Create a realistic image of exactly {expected_subject_count} fictional consenting adults matching the resolved identities and roles. Do not add any additional person.'\n",
    "        subject_contract='Create a realistic image of exactly two fictional consenting adults matching the resolved identities and roles. Do not add any additional person.' if expected_subject_count == 2 else f'Create a realistic image of exactly {expected_subject_count} fictional consenting adults matching the resolved identities and roles. Do not add any additional person.'\n",
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    if vr.must_satisfy:
        sections.append('Must satisfy all requested constraints together: ' + json.dumps(vr.must_satisfy, ensure_ascii=False) + '.')
''',
    '''    prompt_requirements={k:v for k,v in (vr.must_satisfy or {}).items() if v not in (None,'',[],{},False) and 'visibility' not in k and k not in {'identity_visibility_scope','forbidden_regressions'}}
    if prompt_requirements:
        sections.append('Must satisfy all requested constraints together: ' + json.dumps(prompt_requirements, ensure_ascii=False) + '.')
''',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''        sections.append('Hard framing requirement: complete full figure visible from head to feet, entire body inside frame, camera far enough to show the whole body, no tight headshot and no crop at torso, knees, or feet.')
''',
    '''        sections.append('Hard framing requirement: exactly one person when one partner is requested; complete full figure visible from head to feet; entire body inside frame; camera far enough to show the whole body; not a close-up portrait; not a headshot; not cropped at torso, knees, or feet.')
''',
)
text=Path('app/services/image_pipeline_v2.py').read_text(encoding='utf-8')
text=text.replace("'exactly 2 fictional consenting adults'", "'exactly two fictional consenting adults'")
Path('app/services/image_pipeline_v2.py').write_text(text,encoding='utf-8')

# 5. Normalize QA result fields and make the new gate conditional on a real contract.
qa_class='''@dataclass
class GeneratedImageQAResult:
    passed: bool
    person_count: int | None
    face_count: int | None
    second_person_visible: bool
    duplicate_subject_visible: bool
    reflected_person_visible: bool
    background_person_visible: bool
    selfie_detected: bool
    mirror_selfie_detected: bool
    confidence: str
    reason_codes: list[str]
    model: str | None
    requested_clothing_visible: bool | None = None
    requested_scene_visible: bool | None = None
    requested_support_surface_visible: bool | None = None
    requested_pose_matches: bool | None = None
    no_clothing_regression: bool | None = None
    no_unwanted_nudity: bool | None = None
    framing_matches_request: bool | None = None
    identity_consistency_reasonable: bool | None = None
    under_eye_darkness_excessive: bool | None = None
    near_duplicate_composition: bool | None = None
    requested_full_body_visible: bool | None = None
    head_inside_frame: bool | None = None
    feet_inside_frame: bool | None = None
    body_not_cropped: bool | None = None
    requested_eye_contact: bool | None = None
    looking_toward_camera: bool | None = None
    eye_contact_matches_request: bool | None = None
    reflection_visible: bool = False
    reflection_matches_primary_subject: bool | None = None
    reflected_distinct_person_visible: bool = False
    duplicate_identity_in_reflection: bool = False
    anatomy_visible_enough_to_assess: bool | None = None
    anatomy_consistent_with_profile: bool | None = None
    contradictory_sex_characteristics: bool | None = None
    malformed_anatomy: bool | None = None
    implausible_anatomy: bool | None = None
    duplicated_anatomy_parts: bool | None = None
    missing_expected_parts_when_visible: bool | None = None
    ambiguous_anatomy: bool | None = None
    primary_subject_matches_request: bool | None = None
    pet_visible: bool | None = None
    required_objects_visible: bool | None = None
    partner_visible: bool | None = None
    face_visible: bool | None = None
    face_hidden_matches_request: bool | None = None
    back_to_camera_matches_request: bool | None = None
    camera_mode_matches_request: bool | None = None
    natural_capture_plausible: bool | None = None
    looks_like_id_photo: bool | None = None
    hands_only_matches_request: bool | None = None

    def to_metadata(self, *, artifact_checksum: str) -> dict:
        data=asdict(self); data['artifact_checksum']=artifact_checksum
        if hasattr(self, 'raw_provider_reason_codes'): data['raw_provider_reason_codes']=getattr(self, 'raw_provider_reason_codes')
        if hasattr(self, 'qa_passes'): data['qa_passes']=getattr(self, 'qa_passes')
        if hasattr(self, 'consensus_passed'): data['consensus_passed']=getattr(self, 'consensus_passed')
        return data

'''
sub_once('app/services/generated_image_qa_service.py', r'@dataclass\nclass GeneratedImageQAResult:.*?(?=QA_PROMPT=)', qa_class)

qa=Path('app/services/generated_image_qa_service.py'); qtext=qa.read_text(encoding='utf-8')
old='''    if contract.get('primary_subject') in {'pet','object','scene'} and primary_subject_matches is not True: codes.append('primary_subject_mismatch')
    if contract.get('pet_visible') and pet_visible is not True: codes.append('requested_pet_missing')
    if (vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects')) and required_objects_visible is not True: codes.append('required_object_missing')
    if contract.get('partner_visible') is False and partner_visible_detected is not False: codes.append('unexpected_visible_partner')
    if contract.get('face_hidden') and face_hidden_matches is not True: codes.append('face_should_be_hidden')
    if contract.get('face_visible') is True and face_visible_detected is not True: codes.append('face_should_be_visible')
    if contract.get('back_to_camera') and back_matches is not True: codes.append('back_view_mismatch')
    if contract.get('camera_mode') and camera_matches is not True: codes.append('camera_mode_mismatch')
    if contract.get('natural_capture_required', True) and (natural_capture is not True or looks_like_id): codes.append('id_photo_regression' if looks_like_id else 'implausible_camera_capture')
    if contract.get('hands_only') and hands_only_matches is not True: codes.append('hands_only_mismatch')
    if identity_ok is False and contract.get('identity_visibility_scope') != 'absent': codes.append('identity_inconsistent')
'''
new='''    if contract:
        if contract.get('primary_subject') in {'pet','object','scene'} and primary_subject_matches is not True: codes.append('primary_subject_mismatch')
        if contract.get('pet_visible') and pet_visible is not True: codes.append('requested_pet_missing')
        if (vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects')) and required_objects_visible is not True: codes.append('required_object_missing')
        if contract.get('partner_visible') is False and partner_visible_detected is not False: codes.append('unexpected_visible_partner')
        if contract.get('face_hidden') and face_hidden_matches is not True: codes.append('face_should_be_hidden')
        if contract.get('face_visible') is True and face_visible_detected is not True: codes.append('face_should_be_visible')
        if contract.get('back_to_camera') and back_matches is not True: codes.append('back_view_mismatch')
        if contract.get('camera_mode') and camera_matches is not True: codes.append('camera_mode_mismatch')
        if contract.get('natural_capture_required', True) and (natural_capture is not True or looks_like_id): codes.append('id_photo_regression' if looks_like_id else 'implausible_camera_capture')
        if contract.get('hands_only') and hands_only_matches is not True: codes.append('hands_only_mismatch')
    if identity_ok is False and contract.get('identity_visibility_scope') != 'absent': codes.append('identity_inconsistent')
'''
if old not in qtext: raise RuntimeError('QA contract block missing')
qtext=qtext.replace(old,new,1)
assign='''    result.primary_subject_matches_request=primary_subject_matches
    result.pet_visible=pet_visible
    result.required_objects_visible=required_objects_visible
    result.partner_visible=partner_visible_detected
    result.face_visible=face_visible_detected
    result.face_hidden_matches_request=face_hidden_matches
    result.back_to_camera_matches_request=back_matches
    result.camera_mode_matches_request=camera_matches
    result.natural_capture_plausible=natural_capture
    result.looks_like_id_photo=looks_like_id
    result.hands_only_matches_request=hands_only_matches
'''
qtext,count=re.subn(r'    result\.primary_subject_matches_request=primary_subject_matches.*?(?=    if requested_full_body)',assign,qtext,count=1,flags=re.S)
if count != 1: raise RuntimeError(f'QA result assignment cleanup count={count}')
old_meta='''    contract=vr.get('photo_contract') or {}
    if contract.get('primary_subject') in {'pet','object','scene'} and qa.get('primary_subject_matches_request') is not True: return False
    if contract.get('pet_visible') and qa.get('pet_visible') is not True: return False
    if (vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects')) and qa.get('required_objects_visible') is not True: return False
    if contract.get('partner_visible') is False and qa.get('partner_visible') is not False: return False
    if contract.get('face_hidden') and qa.get('face_hidden_matches_request') is not True: return False
    if contract.get('face_visible') is True and qa.get('face_visible') is not True: return False
    if contract.get('back_to_camera') and qa.get('back_to_camera_matches_request') is not True: return False
    if contract.get('camera_mode') and qa.get('camera_mode_matches_request') is not True: return False
    if contract.get('natural_capture_required', True) and (qa.get('natural_capture_plausible') is not True or qa.get('looks_like_id_photo') is True): return False
    if contract.get('hands_only') and qa.get('hands_only_matches_request') is not True: return False
'''
new_meta='''    contract=vr.get('photo_contract') or {}
    if contract:
        if contract.get('primary_subject') in {'pet','object','scene'} and qa.get('primary_subject_matches_request') is not True: return False
        if contract.get('pet_visible') and qa.get('pet_visible') is not True: return False
        if (vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects')) and qa.get('required_objects_visible') is not True: return False
        if contract.get('partner_visible') is False and qa.get('partner_visible') is not False: return False
        if contract.get('face_hidden') and qa.get('face_hidden_matches_request') is not True: return False
        if contract.get('face_visible') is True and qa.get('face_visible') is not True: return False
        if contract.get('back_to_camera') and qa.get('back_to_camera_matches_request') is not True: return False
        if contract.get('camera_mode') and qa.get('camera_mode_matches_request') is not True: return False
        if contract.get('natural_capture_required', True) and (qa.get('natural_capture_plausible') is not True or qa.get('looks_like_id_photo') is True): return False
        if contract.get('hands_only') and qa.get('hands_only_matches_request') is not True: return False
'''
if old_meta not in qtext: raise RuntimeError('QA metadata block missing')
qtext=qtext.replace(old_meta,new_meta,1)
qtext=qtext.replace('Render exactly one fictional adult matching the stored partner identity.', 'Render exactly one fictional adult matching the stored subject identity.', 1)
qtext=qtext.replace('Preserve the stored adult identity and anatomical profile with coherent realistic body proportions; no malformed, duplicated, contradictory, or ambiguous structure.', 'Preserve the stored adult identity and anatomical profile with anatomically plausible structure and coherent realistic body proportions; no malformed, duplicated, contradictory, or ambiguous structure.', 1)
qa.write_text(qtext,encoding='utf-8')

print('partner photo compatibility cleanup applied')
