from datetime import datetime, timedelta
from app.services import image_pipeline_v2 as v2
from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload
from app.services.image_request_state_machine import ImageRequestState, begin_or_update_chain, is_duplicate_command, mark_state


def _profile():
    return v2.ReadOnlyProfileAdapter(user_id=7, partner_name='Amir', fictional_age=27, base_seed=4242, gender_presentation='adult man', face_description='oval face', hair_description='dark short hairline', eye_description='brown almond eyes', skin_description='warm olive skin', body_description='average build', distinguishing_details='defined eyebrows')


def _plan(text, source=None):
    intent=v2.parse_image_intent(v2.normalize_request_v2(text, user_id=7, chat_id=9))
    merged=v2.merge_image_intent(intent, source)
    plan=v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), _profile(), source_job=None, message_id=1, user_request=text)
    return intent, plan, v2.compile_image_prompt(plan)


def test_clear_black_suit_request_no_clarification():
    intent, plan, _ = _plan('عکس بده با کت شلوار مشکیش')
    assert intent.is_image_request
    assert intent.parse_coverage.disposition != v2.ParseDisposition.CLARIFICATION_REQUIRED
    assert plan.wardrobe.value == 'black suit'
    assert plan.visual_requirements.wardrobe_visibility_required


def test_clear_sofa_home_request_no_clarification():
    intent, plan, _ = _plan('رو مبل خونه هم بشین عکس بده')
    assert intent.is_image_request
    assert intent.parse_coverage.disposition != v2.ParseDisposition.CLARIFICATION_REQUIRED
    assert plan.scene.value == 'sofa'
    assert plan.support_surface.value == 'sofa'
    assert plan.visual_requirements.environment_visibility_required


def test_black_suit_sofa_seated_new_constraints_preserved():
    _, plan, compiled = _plan('عکس بده رو مبل خونه نشسته باشی جدید با کت شلوار مشکی')
    assert plan.action == v2.ImageAction.NEW_GENERATION
    assert plan.wardrobe.value == 'black suit'
    assert plan.scene.value == 'sofa'
    assert plan.support_surface.value == 'sofa'
    assert plan.pose.value == 'seated'
    must = plan.visual_requirements.must_satisfy
    assert must['required_wardrobe_elements'] == ['black suit']
    assert must['required_support_surface_elements'] == ['sofa']
    assert 'Must satisfy all requested constraints together' in compiled.positive_prompt


def test_current_sofa_overrides_stale_street_context():
    stale = v2.ResolvedImagePlan(scene=v2.ResolvedField('street', v2.Provenance.SOURCE_PLAN), support_surface=v2.ResolvedField('standing', v2.Provenance.SOURCE_PLAN), pose=v2.ResolvedField('standing', v2.Provenance.SOURCE_PLAN))
    intent=v2.parse_image_intent(v2.normalize_request_v2('عکس بده رو مبل خونه نشسته باشی جدید'))
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent, stale), v2.SafetyDecision(), _profile(), user_request='عکس بده رو مبل خونه نشسته باشی جدید')
    assert plan.scene.value == 'sofa'
    assert plan.pose.value == 'seated'


def test_followup_sofa_preserves_previous_black_suit():
    _, source_plan, _ = _plan('عکس بده با کت شلوار مشکیش')
    intent=v2.parse_image_intent(v2.normalize_request_v2('رو مبل خونه هم بشین عکس بده'))
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent, source_plan), v2.SafetyDecision(), _profile(), user_request='رو مبل خونه هم بشین عکس بده')
    assert plan.wardrobe.value == 'black suit'
    assert 'shirtless' in plan.visual_requirements.forbidden_regressions


def test_generate_new_preserves_identity_but_changes_seed_with_constraints():
    _, p1, _ = _plan('عکس بده با کت شلوار مشکی')
    _, p2, _ = _plan('عکس بده روی مبل خونه نشسته باشی جدید')
    assert p1.identity['identity_fingerprint'] == p2.identity['identity_fingerprint']
    assert p1.seed_strategy['seed_family'] == p2.seed_strategy['seed_family']
    assert p1.seed_strategy['final_provider_seed'] != p2.seed_strategy['final_provider_seed']


def test_same_partner_three_requests_identity_stable():
    fps=[_plan(t)[1].identity['identity_fingerprint'] for t in ['عکس بده','عکس بده با کت شلوار مشکی','عکس بده رو مبل خونه نشسته باشی جدید']]
    assert len(set(fps)) == 1


def test_non_adult_clothed_request_forbids_shirtless_regression():
    _, plan, compiled = _plan('عکس بده با کت شلوار مشکی')
    assert plan.current_intent['content_classification'] == v2.ContentClassification.NORMAL
    assert 'shirtless' in compiled.negative_prompt
    assert 'unwanted nudity' in compiled.negative_prompt


def test_duplicate_command_and_single_clarification_chain_behaviour():
    chain=begin_or_update_chain(None, user_id=1, action='generate_new', text='عکس بده')
    mark_state(chain, ImageRequestState.AWAITING_CLARIFICATION, clarification_target={'reason':'source'})
    assert is_duplicate_command(chain, 'عکس بده')
    updated=begin_or_update_chain(None, user_id=1, action='generate_new', text='جدید', active=chain)
    assert updated.request_chain_id == chain.request_chain_id
    assert updated.current_image_state == ImageRequestState.PENDING_NEW_IMAGE


def test_insufficient_balance_pause_and_resume_keeps_original_intent():
    chain=begin_or_update_chain(None, user_id=1, action='generate_new', text='عکس بده با کت شلوار مشکی')
    mark_state(chain, ImageRequestState.AWAITING_WALLET_TOPUP)
    resumed=begin_or_update_chain(None, user_id=1, action='generate_new', text='پرداخت کردم', active=chain)
    resumed.resumed_after_topup=True
    assert resumed.request_chain_id == chain.request_chain_id
    assert resumed.original_user_intent_snapshot['request_hash']
    assert resumed.resumed_after_topup


def test_qa_fulfillment_failures():
    vr=_plan('عکس بده رو مبل خونه نشسته باشی جدید با کت شلوار مشکی')[1].visual_requirements
    payload={'person_count':1,'face_count':1,'confidence':'high','requested_clothing_visible':False,'requested_scene_visible':False,'requested_support_surface_visible':False,'requested_pose_matches':False,'identity_consistency_reasonable':True,'no_clothing_regression':False,'no_unwanted_nudity':False}
    result=evaluate_generated_image_composition_payload(payload, expected_subject_count=1, visual_requirements=v2.asdict(vr))
    assert {'requested_clothing_not_visible','requested_scene_not_visible','requested_support_surface_not_visible','requested_pose_mismatch','clothing_regression','unwanted_nudity'} <= set(result.reason_codes)


def test_qa_near_duplicate_for_generate_new():
    vr=v2.asdict(_plan('عکس بده جدید')[1].visual_requirements)
    result=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'confidence':'high','near_duplicate_composition':True}, expected_subject_count=1, visual_requirements=vr)
    assert 'near_duplicate_composition' in result.reason_codes
