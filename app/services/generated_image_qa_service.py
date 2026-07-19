from __future__ import annotations
import hashlib, logging
from dataclasses import dataclass, asdict
from app.core.config import get_settings
from app.llm.vision_client import analyze_image_bytes_with_venice

logger=logging.getLogger(__name__)

REASON_CODES={
 'missing_primary_subject','missing_secondary_subject','missing_subject','too_many_people','multiple_people','extra_face','unrelated_background_person','background_person','reflected_extra_person','reflected_person','duplicate_subject','unexpected_selfie','unexpected_mirror_selfie','requested_interaction_missing','requested_clothing_not_visible','requested_scene_not_visible','framing_mismatch','too_close_for_outfit','identity_inconsistent','excessive_under_eye_darkness','near_duplicate_composition','requested_support_surface_not_visible','requested_pose_mismatch','wrong_scene','clothing_regression','unwanted_nudity','qa_uncertain','qa_provider_failure'
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

    def to_metadata(self, *, artifact_checksum: str) -> dict:
        data=asdict(self); data['artifact_checksum']=artifact_checksum
        if hasattr(self, 'raw_provider_reason_codes'):
            data['raw_provider_reason_codes']=getattr(self, 'raw_provider_reason_codes')
        return data

QA_PROMPT='''You are a structural image composition QA module. Return JSON only. Do not identify anyone. Do not discuss nudity or sexual details. Count every real-looking human figure, including partial people, background people, hands/limbs from another person, and people in mirrors or reflections. Distinguish intended subjects from duplicated/twin renderings and unrelated background people. If a requested interaction is supplied, report whether it is visible. Schema: {"person_count":2,"face_count":2,"intended_subject_count":2,"unexpected_additional_person_visible":false,"background_extra_person_visible":false,"duplicate_subject_visible":false,"reflected_extra_person_visible":false,"second_person_visible":true,"selfie_detected":false,"mirror_selfie_detected":false,"interaction_detected":"kiss","interaction_matches_request":true,"confidence":"high","reason_codes":[]}.'''

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
    reflected=_bool(payload.get('reflected_extra_person_visible', payload.get('reflected_person_visible')))
    background=_bool(payload.get('background_extra_person_visible', payload.get('background_person_visible')))
    unexpected_additional=_bool(payload.get('unexpected_additional_person_visible')) or (second and expected_subject_count == 1)
    selfie=_bool(payload.get('selfie_detected')); mirror_selfie=_bool(payload.get('mirror_selfie_detected'))
    confidence=str(payload.get('confidence') or 'low').lower()
    if confidence not in {'low','medium','high'}: malformed=True; confidence='low'
    raw_codes=[str(c) for c in (payload.get('reason_codes') or []) if str(c)]
    codes=[]
    if malformed or person_count is None: codes.append('qa_uncertain')
    if person_count == 0: codes.extend(['missing_primary_subject','missing_subject'])
    if person_count is not None and person_count > expected_subject_count: codes.extend(['too_many_people','multiple_people'])
    if person_count is not None and person_count < expected_subject_count and expected_subject_count > 0: codes.append('missing_secondary_subject' if expected_subject_count > 1 and person_count >= 1 else 'missing_primary_subject')
    if intended_subject_count is not None and intended_subject_count < expected_subject_count: codes.append('missing_secondary_subject' if expected_subject_count > 1 else 'missing_primary_subject')
    if face_count is not None and face_count > expected_subject_count * (2 if mirror_allowed else 1): codes.append('extra_face')
    if unexpected_additional: codes.append('too_many_people')
    if background: codes.extend(['unrelated_background_person','background_person'])
    if reflected and not mirror_allowed: codes.extend(['reflected_extra_person','reflected_person'])
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
    identity_ok=None if payload.get('identity_consistency_reasonable') is None else _bool(payload.get('identity_consistency_reasonable'))
    under_eye_excessive=_bool(payload.get('under_eye_darkness_excessive'))
    near_duplicate=_bool(payload.get('near_duplicate_composition')) or (previous_metadata and previous_metadata.get('seed_family') == payload.get('seed_family') and previous_metadata.get('framing') == payload.get('framing') and previous_metadata.get('camera') == payload.get('camera'))
    if wardrobe_required and requested_clothing_visible is False: codes.append('requested_clothing_not_visible')
    if wardrobe_required and (framing_matches_request is False or payload.get('framing') in {'closeup','tight_headshot','face_only'}): codes.append('too_close_for_outfit')
    if vr.get('visibility_targets',{}).get('environment_visible') and requested_scene_visible is False: codes.extend(['requested_scene_not_visible','wrong_scene'])
    must=vr.get('must_satisfy') or {}
    if must.get('required_support_surface_elements') and requested_support_surface_visible is False: codes.append('requested_support_surface_not_visible')
    if must.get('required_pose_elements') and requested_pose_matches is False: codes.append('requested_pose_mismatch')
    if no_clothing_regression is False: codes.append('clothing_regression')
    if no_unwanted_nudity is False: codes.append('unwanted_nudity')
    if framing_matches_request is False: codes.append('framing_mismatch')
    if identity_ok is False: codes.append('identity_inconsistent')
    if under_eye_excessive and 'under_eye_too_dark' not in (vr.get('correction_signals') or []): codes.append('excessive_under_eye_darkness')
    if vr.get('requested_action') in {'generate_new','new_generation'} and near_duplicate: codes.append('near_duplicate_composition')
    codes=list(dict.fromkeys(codes)); result=GeneratedImageQAResult(not codes, person_count, face_count, second, duplicate, reflected, background, selfie, mirror_selfie, confidence, codes, model or payload.get('model'), requested_clothing_visible, requested_scene_visible, framing_matches_request, identity_ok, under_eye_excessive, near_duplicate)
    result.requested_support_surface_visible=requested_support_surface_visible; result.requested_pose_matches=requested_pose_matches; result.no_clothing_regression=no_clothing_regression; result.no_unwanted_nudity=no_unwanted_nudity
    if {'requested_scene_not_visible','requested_clothing_not_visible','requested_support_surface_not_visible','requested_pose_mismatch','near_duplicate_composition'} & set(codes): logger.info('IMAGE_QA_FULFILLMENT_FAILED user_id=%s request_chain_id=%s action=%s reason_code=%s fulfillment_failure_codes=%s continuity_mode=%s', None, None, vr.get('requested_action'), 'fulfillment_failed', codes, vr.get('requested_action'))
    if 'near_duplicate_composition' in codes: logger.info('IMAGE_NEAR_DUPLICATE_REJECTED user_id=%s request_chain_id=%s action=%s reason_code=%s fulfillment_failure_codes=%s continuity_mode=%s', None, None, vr.get('requested_action'), 'near_duplicate', codes, vr.get('requested_action'))
    if raw_codes: setattr(result, 'raw_provider_reason_codes', raw_codes)
    return result

def evaluate_single_subject_payload(payload: dict, *, expected_subject_count:int=1, selfie_allowed:bool, mirror_allowed:bool, model:str|None=None) -> GeneratedImageQAResult:
    return evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model)

async def evaluate_generated_image_composition(image_bytes: bytes, *, expected_subject_count:int, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['qa_provider_failure','qa_uncertain'],None)
    models=[settings.vision_model]
    if settings.vision_fallback_model and settings.vision_fallback_model not in models: models.append(settings.vision_fallback_model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    for model in models:
        logger.info('IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s', model, checksum)
        try:
            payload=await analyze_image_bytes_with_venice(image_bytes, prompt=QA_PROMPT, model=model)
            result=evaluate_generated_image_composition_payload(payload, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model)
            logger.info('IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', result.model, result.person_count, result.face_count, result.confidence, result.reason_codes, checksum)
            return result
        except Exception:
            logger.warning('IMAGE_GENERATED_QA_COMPLETED qa_model=%s confidence=failed reason_codes=%s artifact_checksum_prefix=%s', model, ['qa_provider_failure'], checksum)
    return GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['qa_provider_failure','qa_uncertain'],None)

async def evaluate_single_subject_image(image_bytes: bytes, *, expected_subject_count:int=1, expected_interaction:str|None=None, selfie_allowed:bool=False, mirror_allowed:bool=False) -> GeneratedImageQAResult:
    return await evaluate_generated_image_composition(image_bytes, expected_subject_count=expected_subject_count, expected_interaction=expected_interaction, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed)

def metadata_has_valid_generated_image_qa(metadata: dict|None, image_bytes: bytes) -> bool:
    qa=(metadata or {}).get('generated_image_qa') or {}
    return bool(qa.get('passed') is True and qa.get('artifact_checksum') == hashlib.sha256(image_bytes or b'').hexdigest())

def corrective_prompt_for_reasons(reason_codes: list[str], *, expected_subject_count:int=1, expected_interaction:str|None=None, secondary_subject_role:str|None=None) -> str:
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
Render exactly one woman alone in the entire frame.
No companion, no photographer, no second person, no background people.
No additional face, head, body, hands or limbs from another person.
No mirror reflection of another person.
No duplicated version of the subject.
The camera must be outside the frame and operated by a tripod or timer.'''
