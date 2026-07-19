from app.services import image_pipeline_v2 as v2
from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload


def _plan(text, action=None, source_job=None):
    intent=v2.parse_image_intent(v2.normalize_request_v2(text, source_message_id=7))
    if action:
        intent.continuity.action=action
    if source_job:
        intent.continuity.source_image_job_id=getattr(source_job,'id',None)
    return v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), v2.ReadOnlyProfileAdapter(base_seed=1234), source_job=source_job, message_id=7, user_request=text)

class Prev:
    id=55; seed=111111; final_provider_seed=111111; user_id=1; chat_id=1


def test_plain_request_not_forced_into_face_only_headshot():
    plan=_plan('یه عکس بده')
    assert plan.visual_requirements.framing_requirement == 'natural_medium_or_medium_wide'
    assert 'not a passport-style centered tight headshot' in v2.compile_image_prompt(plan).positive_prompt


def test_wardrobe_request_requires_visible_non_headshot_framing():
    plan=_plan('اون کت شلوار آبی مشکیش رو هم میخوام ببینم')
    assert plan.visual_requirements.wardrobe_requested is True
    assert plan.visual_requirements.wardrobe_visibility_required is True
    assert plan.composition['framing'] in {'upper_body_or_three_quarter','full_body'}
    prompt=v2.compile_image_prompt(plan).positive_prompt
    assert 'Requested wardrobe must be clearly visible' in prompt
    assert 'Do not use a tight face-only portrait' in prompt


def test_under_eye_critique_extracted_as_correction_signal():
    assert 'under_eye_too_dark' in v2.extract_visual_critique('زیر چشاش چرا کبوده')
    plan=_plan('زیر چشاش چرا کبوده', action=v2.ImageAction.REFINEMENT, source_job=Prev())
    assert 'under_eye_too_dark' in plan.visual_requirements.correction_signals
    assert 'Reduce heavy under-eye darkness' in v2.compile_image_prompt(plan).positive_prompt


def test_negative_feedback_becomes_correction_signal():
    plan=_plan('خوب نبود', action=v2.ImageAction.REFINEMENT, source_job=Prev())
    assert 'negative_feedback' in plan.visual_requirements.correction_signals
    assert plan.continuity_plan.preserve_face_identity is True


def test_generate_new_after_previous_varies_and_seed_branches():
    plan=_plan('یه عکس جدید بده', action=v2.ImageAction.NEW_GENERATION, source_job=Prev())
    assert 'tight_headshot' in plan.continuity_plan.forbidden_repetition_axes
    assert plan.seed_strategy['continuity_mode'] == 'generate_new'
    assert plan.seed_strategy['final_provider_seed'] != Prev.final_provider_seed


def test_variation_preserves_identity_and_changes_composition_axes():
    plan=_plan('یکی دیگه مثل قبلی', action=v2.ImageAction.VARIATION, source_job=Prev())
    assert plan.continuity_plan.preserve_face_identity is True
    assert {'pose','camera','framing'} <= set(plan.continuity_plan.requested_variation_axes)
    assert plan.seed_strategy['final_provider_seed'] != Prev.final_provider_seed


def test_refine_previous_preserves_and_applies_correction():
    plan=_plan('همون قبلی رو بهتر کن زیر چشم تیره نباشه', action=v2.ImageAction.REFINEMENT, source_job=Prev())
    assert plan.continuity_plan.preserve_scene is True
    assert 'under_eye_too_dark' in plan.visual_requirements.correction_signals


def test_requested_suit_not_visible_fails_qa():
    vr={'requested_action':'generate_new','wardrobe_visibility_required':True,'style_targets':{'wardrobe':'blue black suit'}}
    r=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'requested_clothing_visible':False,'confidence':'high'}, expected_subject_count=1, visual_requirements=vr)
    assert not r.passed and 'requested_clothing_not_visible' in r.reason_codes


def test_too_tight_closeup_when_outfit_requested_fails_qa():
    vr={'requested_action':'generate_new','wardrobe_visibility_required':True,'style_targets':{'wardrobe':'suit'}}
    r=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'requested_clothing_visible':True,'framing':'tight_headshot','framing_matches_request':False,'confidence':'high'}, expected_subject_count=1, visual_requirements=vr)
    assert not r.passed and 'too_close_for_outfit' in r.reason_codes


def test_no_closeup_bias_unless_requested_and_selfie_allows_close():
    assert _plan('یه عکس بده').visual_requirements.framing_requirement != 'closeup_allowed'
    assert _plan('یه سلفی بده').visual_requirements.framing_requirement == 'closeup_allowed'


def test_resend_exact_has_no_new_generation_seed():
    plan=_plan('همون عکس رو دوباره بفرست', action=v2.ImageAction.RESEND_EXACT, source_job=Prev())
    assert plan.seed_strategy['final_provider_seed'] is None
    assert plan.seed_strategy['seed_strategy'] == 'reuse_prior_artifact'


def test_under_eye_critique_influences_next_prompt():
    plan=_plan('زیر چشمش تیره است بهترش کن', action=v2.ImageAction.REFINEMENT, source_job=Prev())
    assert 'Reduce heavy under-eye darkness' in v2.compile_image_prompt(plan).positive_prompt
