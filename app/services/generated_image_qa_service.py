from __future__ import annotations
import hashlib, json, logging
from dataclasses import dataclass, asdict
from app.core.config import get_settings
from app.llm.vision_client import analyze_image_bytes_with_venice

logger=logging.getLogger(__name__)

REASON_CODES={
 'missing_primary_subject','missing_secondary_subject','missing_subject','too_many_people','multiple_people','extra_face','unrelated_background_person','background_person','reflected_extra_person','reflected_person','duplicate_subject','unexpected_selfie','unexpected_mirror_selfie','requested_interaction_missing','requested_clothing_not_visible','requested_scene_not_visible','framing_mismatch','too_close_for_outfit','identity_inconsistent','excessive_under_eye_darkness','near_duplicate_composition','requested_support_surface_not_visible','requested_pose_mismatch','wrong_scene','clothing_regression','unwanted_nudity','qa_uncertain','qa_provider_failure','eye_contact_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden','anatomy_profile_missing','anatomy_profile_inconsistent','contradictory_sex_characteristics','malformed_anatomy','ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure'
}

@dataclass
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
    ambiguous_anatomy: bool | None = None

    def to_metadata(self, *, artifact_checksum: str) -> dict:
        data=asdict(self); data['artifact_checksum']=artifact_checksum
        if hasattr(self, 'raw_provider_reason_codes'):
            data['raw_provider_reason_codes']=getattr(self, 'raw_provider_reason_codes')
        return data

QA_PROMPT='''You are a structural image composition QA module. Return JSON only. Do not identify anyone. Do not discuss nudity or sexual details. Count real non-reflected people separately from mirror/reflection appearances. A physically consistent mirror reflection of the same intended subject is not a second real person; a different reflected identity, duplicated/twin rendering, inconsistent extra face/body, or real extra person must be reported. Check whether any requested_scene/requested_location is visibly present. If a requested interaction is supplied, report whether it is visible. Schema: {"person_count":1,"face_count":2,"intended_subject_count":2,"unexpected_additional_person_visible":false,"background_extra_person_visible":false,"duplicate_subject_visible":false,"reflection_visible":true,"reflection_matches_primary_subject":true,"reflected_distinct_person_visible":false,"duplicate_identity_in_reflection":false,"reflected_extra_person_visible":false,"second_person_visible":false,"selfie_detected":false,"mirror_selfie_detected":false,"interaction_detected":"kiss","interaction_matches_request":true,"confidence":"high","framing_matches_request":true,"full_body_visible":true,"head_inside_frame":true,"feet_inside_frame":true,"body_not_cropped":true,"not_closeup":true,"reason_codes":[]}.'''



def _qa_prompt_with_requirements(visual_requirements: dict | None) -> str:
    vr=visual_requirements or {}
    must=vr.get('must_satisfy') or {}
    payload={
        'requested_scene': (must.get('required_scene_elements') or [None])[0],
        'requested_location': (must.get('required_scene_elements') or [None])[0],
        'environment_visibility_required': bool(vr.get('environment_visibility_required')),
        'required_scene_elements': must.get('required_scene_elements') or [],
        'mirrors_allowed': bool(vr.get('mirrors_allowed') or 'mirror' in (must.get('required_scene_elements') or [])),
        'requested_full_body': bool(vr.get('full_body_visible') or vr.get('framing_requirement') == 'full_body'),
        'clothing_visibility_required': bool(vr.get('wardrobe_visibility_required')),
    }
    return QA_PROMPT + "\nActual visual requirements: " + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\nSet requested_scene_visible true/false whenever environment_visibility_required is true."

def _bool(v):
    if isinstance(v, bool): return v
    if v is None: return False
    return str(v).strip().lower() in {'true','1','yes'}

def _int_or_none(v):
    if isinstance(v, bool): return None
    try: i = int(v) if v is not None else None
    except Exception: return None
    return i if i is None or i >= 0 else None

def evaluate_generated_image_composition_payload(payload: dict, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, model:str|None=None, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    malformed=not isinstance(payload, dict); payload=payload if isinstance(payload, dict) else {}
    person_count=_int_or_none(payload.get('person_count')); face_count=_int_or_none(payload.get('face_count'))
    intended_subject_count=_int_or_none(payload.get('intended_subject_count'))
    if payload.get('person_count') is not None and person_count is None: malformed=True
    if payload.get('face_count') is not None and face_count is None: malformed=True
    second=_bool(payload.get('second_person_visible'))
    duplicate=_bool(payload.get('duplicate_subject_visible'))
    reflection_visible=_bool(payload.get('reflection_visible', payload.get('reflected_person_visible')))
    reflection_matches_primary=None if payload.get('reflection_matches_primary_subject') is None else _bool(payload.get('reflection_matches_primary_subject'))
    reflected_distinct=_bool(payload.get('reflected_distinct_person_visible', payload.get('reflected_extra_person_visible')))
    duplicate_reflection=_bool(payload.get('duplicate_identity_in_reflection'))
    reflected=_bool(payload.get('reflected_extra_person_visible', payload.get('reflected_person_visible'))) or reflected_distinct or duplicate_reflection
    background=_bool(payload.get('background_extra_person_visible', payload.get('background_person_visible')))
    unexpected_additional=_bool(payload.get('unexpected_additional_person_visible')) or (second and expected_subject_count == 1)
    selfie=_bool(payload.get('selfie_detected')); mirror_selfie=_bool(payload.get('mirror_selfie_detected'))
    confidence=str(payload.get('confidence') or 'low').lower()
    if confidence not in {'low','medium','high'}: malformed=True; confidence='low'
    raw_codes=[str(c) for c in (payload.get('reason_codes') or []) if str(c)]
    codes=[]
    if malformed or person_count is None: codes.append('qa_uncertain')
    if person_count == 0: codes.extend(['missing_primary_subject','missing_subject'])
    same_subject_reflection_allowed=bool(reflection_visible and reflection_matches_primary is True and not reflected_distinct and not duplicate_reflection)
    adjusted_person_count=person_count
    if same_subject_reflection_allowed and expected_subject_count == 1 and person_count == 2:
        adjusted_person_count=1
    if adjusted_person_count is not None and adjusted_person_count > expected_subject_count: codes.extend(['too_many_people','multiple_people'])
    if adjusted_person_count is not None and adjusted_person_count < expected_subject_count and expected_subject_count > 0: codes.append('missing_secondary_subject' if expected_subject_count > 1 and person_count >= 1 else 'missing_primary_subject')
    if intended_subject_count is not None and intended_subject_count < expected_subject_count: codes.append('missing_secondary_subject' if expected_subject_count > 1 else 'missing_primary_subject')
    if face_count is not None and face_count > expected_subject_count * (2 if (mirror_allowed or same_subject_reflection_allowed) else 1): codes.append('extra_face')
    if unexpected_additional: codes.append('too_many_people')
    if background: codes.extend(['unrelated_background_person','background_person'])
    if reflected_distinct or duplicate_reflection or (reflected and reflection_matches_primary is False): codes.extend(['reflected_extra_person','reflected_person'])
    elif reflected and not (mirror_allowed or same_subject_reflection_allowed): codes.extend(['reflected_extra_person','reflected_person'])
    if duplicate: codes.append('duplicate_subject')
    if expected_interaction and (payload.get('interaction_detected') != expected_interaction or not _bool(payload.get('interaction_matches_request'))): codes.append('requested_interaction_missing')
    if selfie and not selfie_allowed: codes.append('unexpected_selfie')
    if mirror_selfie and not mirror_allowed: codes.append('unexpected_mirror_selfie')
    vr=visual_requirements or {}
    wardrobe_required=bool(vr.get('wardrobe_visibility_required') or (vr.get('style_targets') or {}).get('wardrobe'))
    requested_clothing_visible=None if payload.get('requested_clothing_visible') is None else _bool(payload.get('requested_clothing_visible'))
    requested_scene_visible=None if payload.get('requested_scene_visible') is None else _bool(payload.get('requested_scene_visible'))
    requested_support_surface_visible=None if payload.get('requested_support_surface_visible') is None else _bool(payload.get('requested_support_surface_visible'))
    requested_pose_matches=None if payload.get('requested_pose_matches') is None else _bool(payload.get('requested_pose_matches'))
    no_clothing_regression=None if payload.get('no_clothing_regression') is None else _bool(payload.get('no_clothing_regression'))
    no_unwanted_nudity=None if payload.get('no_unwanted_nudity') is None else _bool(payload.get('no_unwanted_nudity'))
    framing_matches_request=None if payload.get('framing_matches_request') is None else _bool(payload.get('framing_matches_request'))
    requested_full_body=bool(vr.get('full_body_visible') or vr.get('framing_requirement') == 'full_body')
    head_inside_frame=None if payload.get('head_inside_frame') is None else _bool(payload.get('head_inside_frame'))
    feet_inside_frame=None if payload.get('feet_inside_frame') is None else _bool(payload.get('feet_inside_frame'))
    body_not_cropped=None if payload.get('body_not_cropped') is None else _bool(payload.get('body_not_cropped'))
    identity_ok=None if payload.get('identity_consistency_reasonable') is None else _bool(payload.get('identity_consistency_reasonable'))
    under_eye_excessive=_bool(payload.get('under_eye_darkness_excessive'))
    near_duplicate=_bool(payload.get('near_duplicate_composition')) or (previous_metadata and previous_metadata.get('seed_family') == payload.get('seed_family') and previous_metadata.get('framing') == payload.get('framing') and previous_metadata.get('camera') == payload.get('camera'))
    if wardrobe_required and requested_clothing_visible is False: codes.append('requested_clothing_not_visible')
    if wardrobe_required and (framing_matches_request is False or payload.get('framing') in {'closeup','tight_headshot','face_only'}): codes.append('too_close_for_outfit')
    if (vr.get('environment_visibility_required') or vr.get('visibility_targets',{}).get('environment_visible')) and requested_scene_visible is False: codes.extend(['requested_scene_not_visible','wrong_scene'])
    must=vr.get('must_satisfy') or {}
    if must.get('required_support_surface_elements') and requested_support_surface_visible is False: codes.append('requested_support_surface_not_visible')
    if must.get('required_pose_elements') and requested_pose_matches is False: codes.append('requested_pose_mismatch')
    if no_clothing_regression is False: codes.append('clothing_regression')
    if no_unwanted_nudity is False: codes.append('unwanted_nudity')
    if requested_full_body:
        if payload.get('framing') in {'closeup','tight_headshot','face_only','shoulders_only'}: codes.extend(['framing_mismatch','closeup_forbidden'])
        if head_inside_frame is not True: codes.extend(['missing_full_body','missing_head'])
        if feet_inside_frame is not True: codes.append('missing_feet')
        if body_not_cropped is not True: codes.append('cropped_body')
        if framing_matches_request is not True: codes.append('framing_mismatch')
    elif framing_matches_request is False: codes.append('framing_mismatch')
    if identity_ok is False: codes.append('identity_inconsistent')
    if under_eye_excessive and 'under_eye_too_dark' not in (vr.get('correction_signals') or []): codes.append('excessive_under_eye_darkness')
    if vr.get('requested_action') in {'generate_new','new_generation'} and near_duplicate: codes.append('near_duplicate_composition')
    requested_eye_contact=bool(vr.get('eye_contact_required'))
    looking_toward_camera=None if payload.get('looking_toward_camera') is None else _bool(payload.get('looking_toward_camera'))
    eye_contact_matches_request=None if payload.get('eye_contact_matches_request') is None else _bool(payload.get('eye_contact_matches_request'))
    if requested_eye_contact and (looking_toward_camera is not True or eye_contact_matches_request is False): codes.append('eye_contact_mismatch')
    codes=list(dict.fromkeys(codes)); result=GeneratedImageQAResult(passed=not codes, person_count=adjusted_person_count, face_count=face_count, second_person_visible=second, duplicate_subject_visible=duplicate, reflected_person_visible=reflected, background_person_visible=background, reflection_visible=reflection_visible, reflection_matches_primary_subject=reflection_matches_primary, reflected_distinct_person_visible=reflected_distinct, duplicate_identity_in_reflection=duplicate_reflection, selfie_detected=selfie, mirror_selfie_detected=mirror_selfie, confidence=confidence, reason_codes=codes, model=model or payload.get('model'), requested_clothing_visible=requested_clothing_visible, requested_scene_visible=requested_scene_visible, requested_support_surface_visible=requested_support_surface_visible, requested_pose_matches=requested_pose_matches, no_clothing_regression=no_clothing_regression, no_unwanted_nudity=no_unwanted_nudity, framing_matches_request=framing_matches_request, identity_consistency_reasonable=identity_ok, under_eye_darkness_excessive=under_eye_excessive, near_duplicate_composition=near_duplicate, requested_full_body_visible=requested_full_body, head_inside_frame=head_inside_frame, feet_inside_frame=feet_inside_frame, body_not_cropped=body_not_cropped, requested_eye_contact=requested_eye_contact, looking_toward_camera=looking_toward_camera, eye_contact_matches_request=eye_contact_matches_request)
    if requested_full_body and not result.passed: logger.info('IMAGE_FULL_BODY_QA_FAILED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s', None, None, None, vr.get('requested_action'), vr.get('framing_requirement'), ','.join(codes))
    if {'requested_scene_not_visible','wrong_scene','requested_clothing_not_visible','requested_support_surface_not_visible','requested_pose_mismatch','near_duplicate_composition'} & set(codes): logger.info('IMAGE_QA_FULFILLMENT_FAILED user_id=%s request_chain_id=%s action=%s reason_code=%s fulfillment_failure_codes=%s continuity_mode=%s', None, None, vr.get('requested_action'), 'fulfillment_failed', codes, vr.get('requested_action'))
    if {'requested_scene_not_visible','wrong_scene'} & set(codes): logger.info('IMAGE_SCENE_QA_FAILED user_id=%s request_chain_id=%s action=%s qa_requested_scene=%s qa_scene_matches_request=%s', None, None, vr.get('requested_action'), (vr.get('must_satisfy') or {}).get('required_scene_elements'), False)
    if same_subject_reflection_allowed and not ({'reflected_extra_person','reflected_person','too_many_people'} & set(codes)): logger.info('IMAGE_SAME_SUBJECT_REFLECTION_ACCEPTED user_id=%s request_chain_id=%s action=%s reflection_visible=%s reflection_matches_primary_subject=%s', None, None, vr.get('requested_action'), reflection_visible, reflection_matches_primary)
    if {'reflected_extra_person','reflected_person'} & set(codes): logger.info('IMAGE_DISTINCT_REFLECTED_PERSON_REJECTED user_id=%s request_chain_id=%s action=%s reflection_visible=%s reflection_matches_primary_subject=%s', None, None, vr.get('requested_action'), reflection_visible, reflection_matches_primary)
    if 'near_duplicate_composition' in codes: logger.info('IMAGE_NEAR_DUPLICATE_REJECTED user_id=%s request_chain_id=%s action=%s reason_code=%s fulfillment_failure_codes=%s continuity_mode=%s', None, None, vr.get('requested_action'), 'near_duplicate', codes, vr.get('requested_action'))
    if raw_codes: setattr(result, 'raw_provider_reason_codes', raw_codes)
    return result

def evaluate_single_subject_payload(payload: dict, *, expected_subject_count:int=1, selfie_allowed:bool, mirror_allowed:bool, model:str|None=None) -> GeneratedImageQAResult:
    return evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model)

async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)
    models=[settings.vision_model]
    if settings.vision_fallback_model and settings.vision_fallback_model not in models: models.append(settings.vision_fallback_model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    for model in models:
        logger.info('IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s', model, checksum)
        try:
            payload=await analyze_image_bytes_with_venice(image_bytes, prompt=_qa_prompt_with_requirements(visual_requirements), model=model)
            result=evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model, visual_requirements=visual_requirements, previous_metadata=previous_metadata)
            logger.info('IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', result.model, result.person_count, result.face_count, result.confidence, result.reason_codes, checksum)
            return result
        except Exception:
            logger.warning('IMAGE_GENERATED_QA_COMPLETED qa_model=%s confidence=failed reason_codes=%s artifact_checksum_prefix=%s', model, ['qa_provider_failure'], checksum)
    return GeneratedImageQAResult(passed=False, person_count=None, face_count=None, second_person_visible=False, duplicate_subject_visible=False, reflected_person_visible=False, background_person_visible=False, selfie_detected=False, mirror_selfie_detected=False, confidence='low', reason_codes=['qa_provider_failure','qa_uncertain'], model=None)

async def evaluate_single_subject_image(image_bytes: bytes, *, expected_subject_count:int=1, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False, visual_requirements:dict|None=None, previous_metadata:dict|None=None) -> GeneratedImageQAResult:
    return await evaluate_generated_image_composition(image_bytes, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, visual_requirements=visual_requirements, previous_metadata=previous_metadata)


ADULT_ANATOMY_QA_PROMPT="""You are a high-level adult anatomy consistency QA module for fictional adults. Return JSON only. Do not describe genitalia or sexual details. Assess only whether visible anatomy is consistent with the stored anatomical_profile enum supplied in requirements. Schema: {"anatomy_visible_enough_to_assess":true,"anatomy_consistent_with_profile":true,"contradictory_sex_characteristics":false,"malformed_anatomy":false,"ambiguous_anatomy":false,"confidence":"high","reason_codes":[]}."""

def evaluate_adult_anatomy_payload(payload: dict, *, anatomical_profile: str, model: str|None=None) -> GeneratedImageQAResult:
    malformed=not isinstance(payload, dict); payload=payload if isinstance(payload, dict) else {}
    visible=None if payload.get('anatomy_visible_enough_to_assess') is None else _bool(payload.get('anatomy_visible_enough_to_assess'))
    consistent=None if payload.get('anatomy_consistent_with_profile') is None else _bool(payload.get('anatomy_consistent_with_profile'))
    contradictory=_bool(payload.get('contradictory_sex_characteristics'))
    malformed_anatomy=_bool(payload.get('malformed_anatomy'))
    ambiguous=_bool(payload.get('ambiguous_anatomy'))
    confidence=str(payload.get('confidence') or 'low').lower()
    codes=[]
    if anatomical_profile in {None,'','unspecified'}: codes.append('anatomy_profile_missing')
    if malformed or confidence not in {'medium','high'}: codes.append('anatomy_qa_provider_failure' if malformed else 'qa_uncertain')
    if visible is not True: codes.append('anatomy_not_assessable')
    if consistent is not True: codes.append('anatomy_profile_inconsistent')
    if contradictory: codes.append('contradictory_sex_characteristics')
    if malformed_anatomy: codes.append('malformed_anatomy')
    if ambiguous: codes.append('ambiguous_anatomy')
    codes=list(dict.fromkeys(codes + [str(c) for c in (payload.get('reason_codes') or []) if str(c) in REASON_CODES]))
    return GeneratedImageQAResult(not codes, None, None, False, False, False, False, False, False, confidence if confidence in {'low','medium','high'} else 'low', codes, model, anatomy_visible_enough_to_assess=visible, anatomy_consistent_with_profile=consistent, contradictory_sex_characteristics=contradictory, malformed_anatomy=malformed_anatomy, ambiguous_anatomy=ambiguous)

async def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        logger.warning('ADULT_ANATOMY_QA_FAILED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, 'low', ['anatomy_qa_provider_failure'])
        return evaluate_adult_anatomy_payload({}, anatomical_profile=anatomical_profile)
    prompt=ADULT_ANATOMY_QA_PROMPT + "\nRequirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)
    logger.info('ADULT_ANATOMY_QA_STARTED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, None, [])
    try:
        payload=await analyze_image_bytes_with_venice(image_bytes, prompt=prompt, model=settings.vision_model)
        result=evaluate_adult_anatomy_payload(payload, anatomical_profile=anatomical_profile, model=settings.vision_model)
    except Exception:
        result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],None)
    logger.info('ADULT_ANATOMY_QA_%s user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', 'PASSED' if result.passed else 'FAILED', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    return result

def metadata_has_valid_generated_image_qa(metadata: dict|None, image_bytes: bytes) -> bool:
    metadata=metadata or {}; qa=metadata.get('generated_image_qa') or {}
    if not bool(qa.get('passed') is True and qa.get('artifact_checksum') == hashlib.sha256(image_bytes or b'').hexdigest()): return False
    vr=metadata.get('visual_requirements') or {}
    if bool(vr.get('explicit_nudity_requested') and vr.get('anatomy_qa_required')):
        aqa=metadata.get('adult_anatomy_qa') or {}
        ok=bool(vr.get('anatomical_profile') not in (None,'','unspecified') and aqa.get('passed') is True and aqa.get('artifact_checksum') == hashlib.sha256(image_bytes or b'').hexdigest() and aqa.get('anatomy_visible_enough_to_assess') is True and aqa.get('anatomy_consistent_with_profile') is True and aqa.get('contradictory_sex_characteristics') is False and aqa.get('malformed_anatomy') is False and aqa.get('ambiguous_anatomy') is False and aqa.get('confidence') in {'medium','high'})
        if not ok: return False
    if bool(vr.get('full_body_visible') or vr.get('framing_requirement') == 'full_body' or metadata.get('full_body_required')):
        return bool(metadata.get('qa_requested_framing') == 'full_body' and qa.get('framing_matches_request') is True and qa.get('requested_full_body_visible') is True and qa.get('head_inside_frame') is True and qa.get('feet_inside_frame') is True and qa.get('body_not_cropped') is True and 'framing_mismatch' not in (qa.get('reason_codes') or []) and 'closeup_forbidden' not in (qa.get('reason_codes') or []))
    return True

def qa_failure_user_message(reason_codes: list[str]) -> str:
    codes=set(reason_codes or [])
    if codes & {'anatomy_profile_missing'}:
        msg='برای ساخت این نوع تصویر، مشخصات بدنی شخصیت باید اول در پروفایل تعیین بشه.'
    elif codes & {'anatomy_profile_inconsistent','contradictory_sex_characteristics','malformed_anatomy','ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure'}:
        msg='این بار جزئیات بدن با هویت ذخیره‌شده شخصیت هماهنگ درنیومد؛ عکس ارسال نشد و سکه‌ات برگشت.'
    elif codes & {'too_many_people','multiple_people','extra_face','duplicate_subject','background_person','unrelated_background_person','reflected_extra_person'}:
        msg='نتونستم این بار عکس رو بدون آدم اضافه درست بسازم؛ سکه‌ات برگشت.'
    elif 'eye_contact_mismatch' in codes:
        msg='این بار نگاه به دوربین درست درنیومد؛ سکه‌ات برگشت.'
    elif codes & {'framing_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden','anatomy_profile_missing','anatomy_profile_inconsistent','contradictory_sex_characteristics','malformed_anatomy','ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure'}:
        msg='کادر عکس چیزی که خواستی نشد؛ سکه‌ات برگشت.'
    elif 'identity_inconsistent' in codes:
        msg='چهره به‌اندازه کافی ثابت درنیومد؛ سکه‌ات برگشت.'
    elif 'qa_provider_failure' in codes:
        msg='سرویس بررسی تصویر این بار جواب نداد؛ سکه‌ات برگشت.'
    else:
        msg='این بار عکس با شرایطی که خواستی درست درنیومد و سکه‌ات برگشت.'
    logger.info('IMAGE_QA_REASON_USER_MESSAGE_SELECTED user_id=%s job_id=%s request_chain_id=%s action=%s job_status=%s reason_codes=%s', None, None, None, None, None, sorted(codes))
    return msg

def corrective_prompt_for_reasons(reason_codes: list[str], *, expected_subject_count:int=1, expected_interaction:str|None=None, secondary_subject_role:str|None=None, identity_requirements:dict|None=None) -> str:
    if not set(reason_codes or []) & REASON_CODES: return ''
    if expected_subject_count == 2:
        interaction_line='They are mutually and consensually kissing.' if expected_interaction == 'kiss' else 'They have the requested consensual romantic interaction.'
        return f'''

STRICT COMPOSITION CORRECTION:
Render exactly two fictional adults: Moones and one adult {secondary_subject_role or 'neighbor'}.
Both are clearly over 25.
{interaction_line}
No third person, no background people, no duplicates, no reflections of additional people.'''
    return '''

STRICT COMPOSITION CORRECTION:
Render exactly one fictional adult matching the stored subject identity.
If the prior QA failed full-body framing, strengthen the retry: full body visible, full figure head-to-feet, camera farther away, no close-up, no crop.

No companion, no photographer, no second person, no background people.
No additional face, head, body, hands or limbs from another person.
No mirror reflection of another person.
No duplicated version of the subject.
The camera must be outside the frame and operated by a tripod or timer.
If adult anatomy QA failed, preserve face identity, age, gender presentation, body build, anatomical profile, requested scene and framing; correct only contradictory, malformed, ambiguous or inconsistent anatomy without changing the stored sex-characteristics profile.'''
