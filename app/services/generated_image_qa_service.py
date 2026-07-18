from __future__ import annotations
import hashlib, json, logging
from dataclasses import dataclass, asdict
from app.core.config import get_settings
from app.llm.vision_client import analyze_image_bytes_with_venice

logger=logging.getLogger(__name__)

REASON_CODES={
 'missing_subject','multiple_people','extra_face','background_person','reflected_person','duplicate_subject','unexpected_selfie','unexpected_mirror_selfie','qa_uncertain','qa_provider_failure'
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

    def to_metadata(self, *, artifact_checksum: str) -> dict:
        data=asdict(self); data['artifact_checksum']=artifact_checksum
        if hasattr(self, 'raw_provider_reason_codes'):
            data['raw_provider_reason_codes']=getattr(self, 'raw_provider_reason_codes')
        return data

QA_PROMPT='''You are a structural image composition QA module. Return JSON only. Do not identify anyone. Do not discuss nudity or sexual details. Count every real-looking human figure, including partial people, background people, hands/limbs from another person, and people in mirrors or reflections. Distinguish a duplicated rendering of the same subject from a real second character. Report only structural composition. Schema: {"person_count":1,"face_count":1,"second_person_visible":false,"duplicate_subject_visible":false,"reflected_person_visible":false,"background_person_visible":false,"selfie_detected":false,"mirror_selfie_detected":false,"confidence":"high","reason_codes":[]}.'''

def _bool(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {'true','1','yes'}

def _int_or_none(v):
    if isinstance(v, bool):
        return None
    try:
        i = int(v) if v is not None else None
    except Exception:
        return None
    return i if i is None or i >= 0 else None

def evaluate_single_subject_payload(payload: dict, *, expected_subject_count:int=1, selfie_allowed:bool, mirror_allowed:bool, model:str|None=None) -> GeneratedImageQAResult:
    required_bool_fields=('second_person_visible','duplicate_subject_visible','reflected_person_visible','background_person_visible','selfie_detected','mirror_selfie_detected')
    malformed=not isinstance(payload, dict)
    payload = payload if isinstance(payload, dict) else {}
    person_count=_int_or_none(payload.get('person_count'))
    face_count=_int_or_none(payload.get('face_count'))
    if payload.get('person_count') is not None and person_count is None:
        malformed=True
    if payload.get('face_count') is not None and face_count is None:
        malformed=True
    second=_bool(payload.get('second_person_visible'))
    duplicate=_bool(payload.get('duplicate_subject_visible'))
    reflected=_bool(payload.get('reflected_person_visible'))
    background=_bool(payload.get('background_person_visible'))
    selfie=_bool(payload.get('selfie_detected'))
    mirror_selfie=_bool(payload.get('mirror_selfie_detected'))
    confidence=str(payload.get('confidence') or 'low').lower()
    if confidence not in {'low','medium','high'}:
        malformed=True
        confidence='low'
    raw_codes=[str(c) for c in (payload.get('reason_codes') or []) if str(c)]
    codes=[]
    if malformed or person_count is None:
        codes.append('qa_uncertain')
    if person_count == 0:
        codes.append('missing_subject')
    if person_count is not None and person_count > expected_subject_count:
        codes.append('multiple_people')
    if person_count is not None and person_count < expected_subject_count and expected_subject_count > 0:
        codes.append('missing_subject')
    if face_count is not None and face_count > (2 if mirror_allowed else 1):
        codes.append('extra_face')
    if second:
        codes.append('multiple_people')
    if background:
        codes.append('background_person')
    if reflected and not mirror_allowed:
        codes.append('reflected_person')
    if duplicate:
        codes.append('duplicate_subject')
    if selfie and not selfie_allowed:
        codes.append('unexpected_selfie')
    if mirror_selfie and not mirror_allowed:
        codes.append('unexpected_mirror_selfie')
    codes=list(dict.fromkeys(codes))
    passed=not codes
    result=GeneratedImageQAResult(passed, person_count, face_count, second, duplicate, reflected, background, selfie, mirror_selfie, confidence, codes, model or payload.get('model'))
    if raw_codes:
        setattr(result, 'raw_provider_reason_codes', raw_codes)
    return result

async def evaluate_single_subject_image(image_bytes: bytes, *, expected_subject_count:int=1, selfie_allowed:bool, mirror_allowed:bool) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        return GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['qa_provider_failure','qa_uncertain'],None)
    models=[settings.vision_model]
    if settings.vision_fallback_model and settings.vision_fallback_model not in models: models.append(settings.vision_fallback_model)
    checksum=hashlib.sha256(image_bytes).hexdigest()[:12]
    last_error=None
    for model in models:
        logger.info('IMAGE_GENERATED_QA_STARTED qa_model=%s artifact_checksum_prefix=%s', model, checksum)
        try:
            payload=await analyze_image_bytes_with_venice(image_bytes, prompt=QA_PROMPT, model=model)
            result=evaluate_single_subject_payload(payload, expected_subject_count=expected_subject_count, selfie_allowed=selfie_allowed, mirror_allowed=mirror_allowed, model=model)
            logger.info('IMAGE_GENERATED_QA_COMPLETED qa_model=%s person_count=%s face_count=%s confidence=%s reason_codes=%s artifact_checksum_prefix=%s', result.model, result.person_count, result.face_count, result.confidence, result.reason_codes, checksum)
            return result
        except Exception as exc:
            last_error=exc
            logger.warning('IMAGE_GENERATED_QA_COMPLETED qa_model=%s confidence=failed reason_codes=%s artifact_checksum_prefix=%s', model, ['qa_provider_failure'], checksum)
    return GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['qa_provider_failure','qa_uncertain'],None)

def metadata_has_valid_generated_image_qa(metadata: dict|None, image_bytes: bytes) -> bool:
    qa=(metadata or {}).get('generated_image_qa') or {}
    return bool(qa.get('passed') is True and qa.get('artifact_checksum') == hashlib.sha256(image_bytes or b'').hexdigest())

def corrective_prompt_for_reasons(reason_codes: list[str]) -> str:
    if not set(reason_codes or []) & {'missing_subject','multiple_people','extra_face','background_person','reflected_person','duplicate_subject','unexpected_selfie','unexpected_mirror_selfie','qa_uncertain','qa_provider_failure'}:
        return ''
    return '''\n\nSTRICT COMPOSITION CORRECTION:\nRender exactly one woman alone in the entire frame.\nNo companion, no photographer, no second person, no background people.\nNo additional face, head, body, hands or limbs from another person.\nNo mirror reflection of another person.\nNo duplicated version of the subject.\nThe camera must be outside the frame and operated by a tripod or timer.'''
