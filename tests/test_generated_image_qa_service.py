import pytest
from app.services.generated_image_qa_service import evaluate_single_subject_payload, metadata_has_valid_generated_image_qa, GeneratedImageQAResult


def test_background_person_rejected():
    r=evaluate_single_subject_payload({'person_count':2,'face_count':2,'background_person_visible':True,'confidence':'high'}, selfie_allowed=False, mirror_allowed=False)
    assert not r.passed and 'background_person' in r.reason_codes and 'multiple_people' in r.reason_codes


def test_reflected_second_person_rejected():
    r=evaluate_single_subject_payload({'person_count':1,'face_count':2,'reflected_person_visible':True,'confidence':'high'}, selfie_allowed=False, mirror_allowed=False)
    assert not r.passed and 'reflected_person' in r.reason_codes


def test_duplicate_subject_rejected():
    r=evaluate_single_subject_payload({'person_count':1,'face_count':2,'duplicate_subject_visible':True,'confidence':'high'}, selfie_allowed=False, mirror_allowed=False)
    assert not r.passed and 'duplicate_subject' in r.reason_codes


def test_selfie_allowed_accepts_one_person_selfie():
    r=evaluate_single_subject_payload({'person_count':1,'face_count':1,'selfie_detected':True,'confidence':'high'}, selfie_allowed=True, mirror_allowed=False)
    assert r.passed


def test_unexpected_selfie_rejected_for_tripod_plan():
    r=evaluate_single_subject_payload({'person_count':1,'face_count':1,'selfie_detected':True,'confidence':'high'}, selfie_allowed=False, mirror_allowed=False)
    assert not r.passed and 'unexpected_selfie' in r.reason_codes


def test_own_mirror_reflection_requires_mirror_plan():
    no=evaluate_single_subject_payload({'person_count':1,'face_count':2,'mirror_selfie_detected':True,'confidence':'high'}, selfie_allowed=False, mirror_allowed=False)
    yes=evaluate_single_subject_payload({'person_count':1,'face_count':2,'mirror_selfie_detected':True,'confidence':'high'}, selfie_allowed=True, mirror_allowed=True)
    extra=evaluate_single_subject_payload({'person_count':2,'face_count':3,'second_person_visible':True,'mirror_selfie_detected':True,'confidence':'high'}, selfie_allowed=True, mirror_allowed=True)
    assert not no.passed and 'unexpected_mirror_selfie' in no.reason_codes
    assert yes.passed
    assert not extra.passed


def test_delivery_checksum_guard_blocks_mismatch():
    qa=GeneratedImageQAResult(True,1,1,False,False,False,False,False,False,'high',[], 'm')
    import hashlib
    assert metadata_has_valid_generated_image_qa({'generated_image_qa':qa.to_metadata(artifact_checksum=hashlib.sha256(b'a').hexdigest())}, b'a')
    assert not metadata_has_valid_generated_image_qa({'generated_image_qa':qa.to_metadata(artifact_checksum=hashlib.sha256(b'a').hexdigest())}, b'b')

def test_qa_primary_vision_model_fails_fallback_is_tried(monkeypatch):
    import asyncio

    async def run():
        import app.services.generated_image_qa_service as svc

        class S:
            venice_api_key='x'
            vision_model='primary-vl'
            vision_fallback_model='fallback-vl'

        calls=[]
        monkeypatch.setattr(svc, 'get_settings', lambda: S())

        async def fake_analyze(image_bytes, *, prompt=None, model=None):
            calls.append(model)
            if model == 'primary-vl':
                raise RuntimeError('boom')
            return {'person_count':1,'face_count':1,'confidence':'high'}

        monkeypatch.setattr(svc, 'analyze_image_bytes_with_venice', fake_analyze)
        result=await svc.evaluate_single_subject_image(b'img', selfie_allowed=False, mirror_allowed=False)
        assert result.passed and result.model == 'fallback-vl'
        assert calls == ['primary-vl','fallback-vl']

    asyncio.run(run())


def test_qa_both_models_fail_returns_provider_failure(monkeypatch):
    import asyncio

    async def run():
        import app.services.generated_image_qa_service as svc

        class S:
            venice_api_key='x'
            vision_model='primary-vl'
            vision_fallback_model='fallback-vl'

        monkeypatch.setattr(svc, 'get_settings', lambda: S())

        async def fail(*a, **k):
            raise RuntimeError('bad json')

        monkeypatch.setattr(svc, 'analyze_image_bytes_with_venice', fail)
        result=await svc.evaluate_single_subject_image(b'img', selfie_allowed=False, mirror_allowed=False)
        assert not result.passed and 'qa_provider_failure' in result.reason_codes

    asyncio.run(run())


def test_person_count_zero_is_missing_subject_not_multiple_people():
    r=evaluate_single_subject_payload({'person_count':0,'face_count':0,'confidence':'high','reason_codes':['multiple_people']}, selfie_allowed=False, mirror_allowed=False)
    assert not r.passed
    assert 'missing_subject' in r.reason_codes
    assert 'multiple_people' not in r.reason_codes


def test_provider_reason_codes_do_not_override_structured_fields():
    r=evaluate_single_subject_payload({'person_count':1,'face_count':1,'confidence':'high','reason_codes':['multiple_people']}, selfie_allowed=False, mirror_allowed=False)
    assert r.passed
    assert r.reason_codes == []
    assert getattr(r, 'raw_provider_reason_codes') == ['multiple_people']


def test_missing_qa_provider_fails_closed(monkeypatch):
    import asyncio
    async def run():
        import app.services.generated_image_qa_service as svc
        class S:
            venice_api_key=''
            vision_model='primary-vl'
            vision_fallback_model='fallback-vl'
        monkeypatch.setattr(svc, 'get_settings', lambda: S())
        result=await svc.evaluate_single_subject_image(b'img', selfie_allowed=False, mirror_allowed=False)
        assert not result.passed
        assert result.reason_codes == ['qa_provider_failure','qa_uncertain']
    asyncio.run(run())


def test_generated_image_qa_result_keyword_mapping_and_eye_contact_reason():
    from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload, qa_failure_user_message
    vr={'eye_contact_required': True}
    r=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'confidence':'high','looking_toward_camera':False,'eye_contact_matches_request':False}, expected_subject_count=1, visual_requirements=vr)
    assert r.requested_eye_contact is True
    assert r.looking_toward_camera is False
    assert r.eye_contact_matches_request is False
    assert 'eye_contact_mismatch' in r.reason_codes
    assert qa_failure_user_message(r.reason_codes) == 'این بار نگاه به دوربین درست درنیومد؛ سکه‌ات برگشت.'


def test_corrective_prompt_is_identity_safe_and_not_hardcoded_woman():
    from app.services.generated_image_qa_service import corrective_prompt_for_reasons
    prompt=corrective_prompt_for_reasons(['multiple_people'], expected_subject_count=1, identity_requirements={'gender_presentation':'adult man'})
    assert 'woman' not in prompt.lower()
    assert 'Render exactly one fictional adult matching the stored subject identity.' in prompt


def test_adult_anatomy_qa_contract_pass_and_fail():
    from app.services.generated_image_qa_service import evaluate_adult_anatomy_payload
    ok=evaluate_adult_anatomy_payload({'anatomy_visible_enough_to_assess':True,'anatomy_consistent_with_profile':True,'contradictory_sex_characteristics':False,'malformed_anatomy':False,'implausible_anatomy':False,'duplicated_anatomy_parts':False,'missing_expected_parts_when_visible':False,'ambiguous_anatomy':False,'confidence':'high','reason_codes':[]}, anatomical_profile='male')
    assert ok.passed is True
    bad=evaluate_adult_anatomy_payload({'anatomy_visible_enough_to_assess':True,'anatomy_consistent_with_profile':False,'contradictory_sex_characteristics':True,'malformed_anatomy':False,'implausible_anatomy':False,'duplicated_anatomy_parts':False,'missing_expected_parts_when_visible':False,'ambiguous_anatomy':False,'confidence':'high','reason_codes':[]}, anatomical_profile='female')
    assert bad.passed is False
    assert 'anatomy_profile_inconsistent' in bad.reason_codes
    assert 'contradictory_sex_characteristics' in bad.reason_codes


def test_adult_anatomy_delivery_gate_requires_non_graphic_metadata_checksum():
    import hashlib
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa, GeneratedImageQAResult
    data=b'image'
    checksum=hashlib.sha256(data).hexdigest()
    base=GeneratedImageQAResult(True,1,1,False,False,False,False,False,False,'high',[],'qa').to_metadata(artifact_checksum=checksum)
    meta={'visual_requirements':{'explicit_nudity_requested':True,'anatomy_qa_required':True,'anatomical_profile':'male'},'generated_image_qa':base,'adult_anatomy_qa':{'passed':True,'artifact_checksum':checksum,'anatomy_visible_enough_to_assess':True,'anatomy_consistent_with_profile':True,'contradictory_sex_characteristics':False,'malformed_anatomy':False,'implausible_anatomy':False,'duplicated_anatomy_parts':False,'missing_expected_parts_when_visible':False,'ambiguous_anatomy':False,'confidence':'high','reason_codes':[],'consensus_passed':True,'qa_passes':[{'model':'vision-primary','passed':True,'confidence':'high','reason_codes':[]},{'model':'vision-fallback','passed':True,'confidence':'high','reason_codes':[]}]}}
    assert metadata_has_valid_generated_image_qa(meta, data)
    meta['adult_anatomy_qa']['ambiguous_anatomy']=True
    assert not metadata_has_valid_generated_image_qa(meta, data)


def test_adult_anatomy_structural_reason_codes_are_separate_and_fail_closed():
    from app.services.generated_image_qa_service import evaluate_adult_anatomy_payload, qa_failure_user_message, corrective_prompt_for_reasons
    bad=evaluate_adult_anatomy_payload({'anatomy_visible_enough_to_assess':True,'anatomy_consistent_with_profile':True,'contradictory_sex_characteristics':False,'malformed_anatomy':True,'implausible_anatomy':True,'duplicated_anatomy_parts':True,'missing_expected_parts_when_visible':True,'ambiguous_anatomy':False,'confidence':'high','reason_codes':[]}, anatomical_profile='male')
    assert bad.passed is False
    for code in ['malformed_anatomy','implausible_anatomy','duplicated_anatomy_parts','missing_expected_parts_when_visible']:
        assert code in bad.reason_codes
    assert qa_failure_user_message(bad.reason_codes) == 'این بار جزئیات بدن طبیعی و درست درنیومد؛ عکس ارسال نشد و سکه‌ات برگشت.'
    retry=corrective_prompt_for_reasons(bad.reason_codes)
    assert 'anatomically plausible structure' in retry
    assert 'no duplicated anatomy parts' in retry
    assert 'Do not add graphic wording' in retry


def test_explicit_anatomy_gate_requires_completed_structural_qa_fields():
    import hashlib
    from app.services.generated_image_qa_service import metadata_has_valid_generated_image_qa, GeneratedImageQAResult
    data=b'explicit-image'; checksum=hashlib.sha256(data).hexdigest()
    generated=GeneratedImageQAResult(True,1,1,False,False,False,False,False,False,'high',[],'qa').to_metadata(artifact_checksum=checksum)
    meta={'visual_requirements':{'explicit_nudity_requested':True,'anatomy_qa_required':True,'anatomical_profile':'male'},'generated_image_qa':generated,'adult_anatomy_qa':{'passed':True,'artifact_checksum':checksum,'anatomy_visible_enough_to_assess':True,'anatomy_consistent_with_profile':True,'contradictory_sex_characteristics':False,'malformed_anatomy':False,'implausible_anatomy':False,'duplicated_anatomy_parts':False,'missing_expected_parts_when_visible':False,'ambiguous_anatomy':False,'confidence':'high','reason_codes':[],'consensus_passed':True,'qa_passes':[{'model':'vision-primary','passed':True,'confidence':'high','reason_codes':[]},{'model':'vision-fallback','passed':True,'confidence':'high','reason_codes':[]}]}}
    assert metadata_has_valid_generated_image_qa(meta, data)
    for field in ['implausible_anatomy','duplicated_anatomy_parts']:
        broken={**meta, 'adult_anatomy_qa':{**meta['adult_anatomy_qa'], field: True}}
        assert not metadata_has_valid_generated_image_qa(broken, data)
    missing={**meta, 'adult_anatomy_qa':{k:v for k,v in meta['adult_anatomy_qa'].items() if k!='implausible_anatomy'}}
    assert not metadata_has_valid_generated_image_qa(missing, data)
