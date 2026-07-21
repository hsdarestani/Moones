from types import SimpleNamespace

from app.services.partner_photo_contract import (
    build_partner_photo_contract,
    image_acknowledgement,
    image_status_text,
    prompt_constraints,
)
from app.services.semantic_image_intent_router import (
    SemanticImageAction,
    SemanticImageDecision,
    SemanticImageIntentRouter,
    VisualIntent,
    canonical_explicit_image_action,
)
from app.services import image_pipeline_v2 as v2
from app.services.image_generation_service import apply_semantic_visual_intent_to_v2_intent
from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload


def decision(vi):
    return SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW,
        media_delivery_requested=True,
        confidence=.95,
        reason_code='test',
        visual_intent=vi,
    )


def test_detailed_photo_request_keeps_generate_fallback_while_production_extracts_semantics():
    assert canonical_explicit_image_action('یه عکس از قهوه ات بده فقط دستات معلوم باشه') == SemanticImageAction.GENERATE_NEW
    assert canonical_explicit_image_action('عکس بده پشت به دوربین باشی') == SemanticImageAction.GENERATE_NEW


def test_low_confidence_straightforward_generation_does_not_trigger_clarification():
    router=SemanticImageIntentRouter(SimpleNamespace())
    result=router._calibrate(SemanticImageDecision(action='generate_new', media_delivery_requested=True, confidence=.55, reason_code='clear_photo', visual_intent=VisualIntent(primary_subject='pet', pet_only=True)))
    assert result.action == 'generate_new'
    assert not result.needs_clarification


def test_coffee_hands_only_contract_hides_face_and_uses_pov():
    vi=VisualIntent(primary_subject='object', object_only=False, hands_only=True, face_hidden=True, visible_objects=['coffee cup'])
    contract=build_partner_photo_contract(vi)
    assert contract['partner_visible'] is True
    assert contract['hands_only'] is True
    assert contract['face_hidden'] is True
    assert contract['camera_mode'] == 'point_of_view'
    assert contract['framing'] == 'detail'
    assert 'hands' in contract['required_body_regions']


def test_pet_only_contract_has_zero_humans():
    contract=build_partner_photo_contract(VisualIntent(primary_subject='pet', pet_only=True, pet_visible=True))
    assert contract['primary_subject'] == 'pet'
    assert contract['partner_visible'] is False
    assert contract['expected_human_subject_count'] == 0


def test_full_body_selfie_becomes_plausible_mirror_selfie():
    contract=build_partner_photo_contract(VisualIntent(primary_subject='partner', camera_mode='selfie', framing='full_body'))
    assert contract['camera_mode'] == 'mirror_selfie'
    assert contract['framing'] == 'full_body'


def test_back_view_contract_does_not_force_front_face():
    contract=build_partner_photo_contract(VisualIntent(back_to_camera=True, framing='full_body'))
    assert contract['back_to_camera'] is True
    assert contract['face_hidden'] is True
    assert 'face' in contract['forbidden_body_regions']


def test_semantic_contract_is_copied_into_v2_intent():
    intent=v2.ImageRequestIntent(is_image_request=True)
    vi=VisualIntent(primary_subject='pet', pet_only=True, pet_visible=True, camera_mode='point_of_view', visible_objects=['cat'])
    apply_semantic_visual_intent_to_v2_intent(intent, decision(vi))
    assert intent.expected_subject_count == 0
    assert intent.photo_contract['pet_only'] is True
    assert intent.composition.camera == 'point_of_view'
    assert any(r.object == 'cat' for r in intent.scene.spatial_relations)


def test_object_only_prompt_has_no_generic_portrait():
    intent=v2.ImageRequestIntent(is_image_request=True)
    vi=VisualIntent(primary_subject='object', object_only=True, partner_visible=False, visible_objects=['coffee cup'], camera_mode='point_of_view')
    apply_semantic_visual_intent_to_v2_intent(intent, decision(vi))
    profile=v2.ReadOnlyProfileAdapter(gender_presentation='adult man')
    vr=v2.resolve_visual_requirements(intent, user_request='x')
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), profile, message_id=10, user_request='x')
    compiled=v2.compile_image_prompt(plan)
    assert plan.composition['expected_subject_count'] == 0
    assert 'zero visible human people' in compiled.positive_prompt
    assert 'passport photo' in compiled.negative_prompt
    assert 'coffee cup' in compiled.positive_prompt


def test_qa_accepts_zero_people_for_pet_only_and_rejects_visible_partner():
    vr={'photo_contract': {'primary_subject':'pet','pet_visible':True,'partner_visible':False,'camera_mode':'point_of_view','natural_capture_required':True}, 'pet_visible':True, 'partner_visible':False, 'camera_mode':'point_of_view', 'natural_capture_required':True}
    good=evaluate_generated_image_composition_payload({'person_count':0,'face_count':0,'confidence':'high','primary_subject_matches_request':True,'pet_visible':True,'partner_visible':False,'camera_mode_matches_request':True,'natural_capture_plausible':True,'looks_like_id_photo':False}, expected_subject_count=0, visual_requirements=vr)
    assert good.passed, good.reason_codes
    bad=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'confidence':'high','primary_subject_matches_request':True,'pet_visible':True,'partner_visible':True,'camera_mode_matches_request':True,'natural_capture_plausible':True,'looks_like_id_photo':False}, expected_subject_count=0, visual_requirements=vr)
    assert 'unexpected_visible_partner' in bad.reason_codes


def test_qa_rejects_id_headshot_and_wrong_face_visibility():
    vr={'photo_contract': {'primary_subject':'partner','partner_visible':True,'face_hidden':True,'camera_mode':'tripod_timer','natural_capture_required':True,'identity_visibility_scope':'partial'}, 'face_hidden_required':True, 'camera_mode':'tripod_timer', 'natural_capture_required':True}
    result=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'confidence':'high','partner_visible':True,'face_hidden_matches_request':False,'camera_mode_matches_request':False,'natural_capture_plausible':False,'looks_like_id_photo':True}, expected_subject_count=1, visual_requirements=vr)
    assert {'face_should_be_hidden','camera_mode_mismatch','id_photo_regression'} <= set(result.reason_codes)


def test_partner_photo_messages_are_human_not_queue_copy():
    ack=image_acknowledgement({'visual_requirements':{'photo_contract':{'primary_subject':'pet'}},'content_classification':'normal'})
    assert 'صف' not in ack and 'ثبت' not in ack
    status=image_status_text('queued')
    assert 'صف' not in status and 'درخواست' not in status


def test_prompt_constraints_keep_identity_optional_when_partner_absent():
    lines=' '.join(prompt_constraints(build_partner_photo_contract(VisualIntent(primary_subject='pet', pet_only=True, pet_visible=True))))
    assert 'No human person is visible' in lines
    assert 'pet is the primary subject' in lines
