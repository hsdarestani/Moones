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


def test_semantic_full_body_trace_prompt_and_qa_enforcement():
    intent=v2.parse_image_intent(v2.normalize_request_v2('یه عکس بده'))
    intent.is_image_request=True
    intent.composition.framing='full_body'
    intent.body_visibility.regions.setdefault('full_body', v2.BodyRegionIntent(mentioned=True, visibility_requested=True, framing_requested=True, explicit_current_request=True))
    plan=v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), v2.ReadOnlyProfileAdapter(base_seed=1234), message_id=7, user_request='یه عکس بده قدی ببینمت')
    compiled=v2.compile_image_prompt(plan)
    assert plan.visual_requirements.framing_requirement == 'full_body'
    assert plan.visual_requirements.full_body_visible is True
    assert plan.visual_requirements.head_visible is True
    assert plan.visual_requirements.feet_visible is True
    assert 'complete full figure visible from head to feet' in compiled.positive_prompt
    assert 'tight headshot' in compiled.negative_prompt
    assert 'missing feet' in compiled.negative_prompt
    face_only=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'framing':'tight_headshot','framing_matches_request':False,'head_inside_frame':True,'feet_inside_frame':False,'body_not_cropped':False,'confidence':'high'}, expected_subject_count=1, visual_requirements=v2.asdict(plan.visual_requirements))
    assert not face_only.passed
    assert {'framing_mismatch','missing_feet','cropped_body'} <= set(face_only.reason_codes)
    valid=evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'framing':'full_body','framing_matches_request':True,'head_inside_frame':True,'feet_inside_frame':True,'body_not_cropped':True,'confidence':'high'}, expected_subject_count=1, visual_requirements=v2.asdict(plan.visual_requirements))
    assert valid.passed


def test_job_97_persian_full_body_request_regression_end_to_end_planning():
    text = 'یه عکس بده قدی ببینمت'
    intent = v2.parse_image_intent(v2.normalize_request_v2(text))
    assert intent.route.action == v2.ImageAction.NEW_GENERATION
    assert intent.composition.framing == 'full_body'
    merged = v2.merge_image_intent(intent)
    assert merged['framing'].value == 'full_body'
    plan = v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), v2.ReadOnlyProfileAdapter(base_seed=1234), message_id=97, user_request=text)
    compiled = v2.compile_image_prompt(plan)
    assert plan.composition['framing'] == 'full_body'
    assert plan.visual_requirements.framing_requirement == 'full_body'
    for k in ['full_body_visible','head_visible','feet_visible','body_not_cropped','closeup_forbidden','tight_portrait_forbidden']:
        assert plan.visual_requirements.must_satisfy[k] is True
    for term in ['exactly one person','complete full figure visible from head to feet','entire body inside frame','camera far enough to show the whole body','not a close-up portrait','not a headshot','not cropped at torso, knees, or feet']:
        assert term in compiled.positive_prompt
    for term in ['close-up','headshot','face-only portrait','shoulders-only crop','body cropped out of frame','missing legs','missing feet']:
        assert term in compiled.negative_prompt
    assert v2.validate_compiled_prompt(plan, compiled) == []


def test_job_97_mocked_headshot_fails_and_full_body_passes():
    vr = v2.asdict(_plan('یه عکس بده قدی ببینمت').visual_requirements)
    headshot = evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'framing':'tight_headshot','framing_matches_request':False,'head_inside_frame':True,'feet_inside_frame':False,'body_not_cropped':False,'confidence':'high'}, expected_subject_count=1, visual_requirements=vr)
    assert not headshot.passed
    assert {'framing_mismatch','closeup_forbidden','missing_feet','cropped_body'} <= set(headshot.reason_codes)
    valid = evaluate_generated_image_composition_payload({'person_count':1,'face_count':1,'framing':'full_body','framing_matches_request':True,'head_inside_frame':True,'feet_inside_frame':True,'body_not_cropped':True,'confidence':'high'}, expected_subject_count=1, visual_requirements=vr)
    assert valid.passed


def test_look_at_camera_maps_to_eye_contact_requirement():
    from app.services import image_pipeline_v2 as v2
    plan=_plan('یه عکس دیگه بده به دوربین نگاه کن')
    assert plan.visual_requirements.gaze_direction == 'toward_camera'
    assert plan.visual_requirements.eye_contact_required is True
    assert 'Eye contact requirement' in v2.compile_image_prompt(plan).positive_prompt
