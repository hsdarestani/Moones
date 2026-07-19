from app.services import image_pipeline_v2 as v2
from app.services.generated_image_qa_service import evaluate_generated_image_composition_payload
from app.services.image_generation_service import apply_semantic_visual_intent_to_v2_intent
from app.services.semantic_image_intent_router import SemanticImageAction, SemanticImageDecision, VisualIntent


def _profile():
    return v2.ReadOnlyProfileAdapter(fictional_age=25, base_seed=42)


def _semantic_bathroom(text, *, nude=False):
    intent = v2.parse_image_intent(v2.normalize_request_v2(text))
    decision = SemanticImageDecision(
        action=SemanticImageAction.GENERATE_NEW,
        media_delivery_requested=True,
        confidence=0.95,
        reason_code='semantic_scene',
        visual_intent=VisualIntent(
            scene='bathroom',
            location='bathroom',
            environment_type='private_indoor',
            privacy='private',
            required_visible_environment_elements=['recognizable bathroom environment'],
            scene_explicit_current_request=True,
            framing='full_body',
        ),
    )
    apply_semantic_visual_intent_to_v2_intent(intent, decision)
    return intent


def _plan(text, intent, routine=None):
    merged = v2.merge_image_intent(intent, routine_context=routine)
    return v2.construct_resolved_plan(intent, merged, v2.SafetyDecision(), _profile(), message_id=1, user_request=text)


def test_semantic_bathroom_full_body_normal_overrides_routine_and_not_passthrough():
    text = 'یه عکس بده تو حموم باشی قدی'
    intent = _semantic_bathroom(text)
    plan = _plan(text, intent, routine={'location': 'کافه‌ی کوچیک نزدیک ولیعصر', 'slot_name': 'evening'})
    assert plan.scene.value == 'bathroom'
    assert plan.location.value == 'bathroom'
    assert plan.scene.source == v2.Provenance.EXPLICIT
    assert plan.visual_requirements.framing_requirement == 'full_body'
    assert plan.current_intent['content_classification'] == v2.ContentClassification.NORMAL
    assert plan.current_intent['adult_intent'] is None
    assert 'حموم' not in plan.passthrough_visual_details
    assert plan.visual_requirements.environment_visibility_required is True
    assert plan.visual_requirements.visibility_targets.environment_visible is True
    assert 'کافه' not in v2.compile_image_prompt(plan).positive_prompt


def test_bathroom_scene_qa_fails_cafe_and_passes_bathroom():
    intent = _semantic_bathroom('یه عکس بده تو حموم باشی قدی')
    vr = v2.asdict(_plan('یه عکس بده تو حموم باشی قدی', intent).visual_requirements)
    cafe = evaluate_generated_image_composition_payload({'person_count': 1, 'face_count': 1, 'requested_scene_visible': False, 'framing_matches_request': True, 'head_inside_frame': True, 'feet_inside_frame': True, 'body_not_cropped': True, 'confidence': 'high'}, expected_subject_count=1, visual_requirements=vr)
    assert cafe.passed is False
    assert {'requested_scene_not_visible', 'wrong_scene'} <= set(cafe.reason_codes)
    bathroom = evaluate_generated_image_composition_payload({'person_count': 1, 'face_count': 1, 'requested_scene_visible': True, 'framing_matches_request': True, 'head_inside_frame': True, 'feet_inside_frame': True, 'body_not_cropped': True, 'confidence': 'high'}, expected_subject_count=1, visual_requirements=vr)
    assert bathroom.passed is True


def test_bathroom_nudity_keeps_scene_but_does_not_require_full_outfit():
    text = 'یه عکس بده تو حموم لخت باشی قدی'
    intent = _semantic_bathroom(text)
    intent.adult_intent = 'full_nudity'
    intent.content_classification = v2.ContentClassification.FULL_NUDITY
    plan = _plan(text, intent)
    assert plan.scene.value == 'bathroom'
    assert plan.current_intent['content_classification'] == v2.ContentClassification.FULL_NUDITY
    assert plan.visual_requirements.full_body_visible is True
    assert plan.visual_requirements.visibility_targets.full_outfit_visible is False
    assert plan.visual_requirements.wardrobe_visibility_required is False


def test_black_suit_full_body_still_requires_full_outfit():
    intent = v2.parse_image_intent(v2.normalize_request_v2('یه عکس کت شلوار مشکی تمام قد بده'))
    plan = _plan('یه عکس کت شلوار مشکی تمام قد بده', intent)
    assert plan.visual_requirements.full_body_visible is True
    assert plan.visual_requirements.visibility_targets.full_outfit_visible is True
    assert plan.visual_requirements.wardrobe_visibility_required is True


def test_reflection_aware_single_subject_qa():
    base = {'requested_scene_visible': True, 'framing_matches_request': True, 'head_inside_frame': True, 'feet_inside_frame': True, 'body_not_cropped': True, 'confidence': 'high'}
    same = evaluate_generated_image_composition_payload({**base, 'person_count': 2, 'face_count': 2, 'reflection_visible': True, 'reflection_matches_primary_subject': True, 'second_person_visible': False}, expected_subject_count=1, visual_requirements={'full_body_visible': True})
    assert same.passed is True
    assert same.person_count == 1
    diff = evaluate_generated_image_composition_payload({**base, 'person_count': 2, 'face_count': 2, 'reflection_visible': True, 'reflection_matches_primary_subject': False, 'reflected_distinct_person_visible': True}, expected_subject_count=1, visual_requirements={'full_body_visible': True})
    assert diff.passed is False
    assert 'reflected_extra_person' in diff.reason_codes
    second = evaluate_generated_image_composition_payload({**base, 'person_count': 2, 'face_count': 2, 'second_person_visible': True}, expected_subject_count=1, visual_requirements={'full_body_visible': True})
    assert second.passed is False
    assert 'too_many_people' in second.reason_codes
    cropped = evaluate_generated_image_composition_payload({**base, 'person_count': 1, 'face_count': 1, 'feet_inside_frame': False}, expected_subject_count=1, visual_requirements={'full_body_visible': True})
    assert cropped.passed is False
    assert 'missing_feet' in cropped.reason_codes
