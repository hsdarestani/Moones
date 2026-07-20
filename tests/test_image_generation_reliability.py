from types import SimpleNamespace

from app.services import image_pipeline_v2 as v2
from app.services.generated_image_qa_service import GeneratedImageQAResult, merge_adult_anatomy_qa_results
from app.services.image_generation_guardrails import (
    apply_adult_scene_policy,
    apply_semantic_safety_contract,
    select_generation_model,
)
from app.services.semantic_image_intent_router import VisualIntent


def _qa(*, passed=True, model='vision-a', confidence='high', **overrides):
    values=dict(
        passed=passed, person_count=None, face_count=None, second_person_visible=False,
        duplicate_subject_visible=False, reflected_person_visible=False,
        background_person_visible=False, selfie_detected=False,
        mirror_selfie_detected=False, confidence=confidence, reason_codes=[], model=model,
        anatomy_visible_enough_to_assess=True, anatomy_consistent_with_profile=True,
        contradictory_sex_characteristics=False, malformed_anatomy=False,
        implausible_anatomy=False, duplicated_anatomy_parts=False,
        missing_expected_parts_when_visible=False, ambiguous_anatomy=False,
    )
    values.update(overrides)
    return GeneratedImageQAResult(**values)


def test_semantic_explicit_anatomy_focus_is_denied_before_generation():
    intent=v2.ImageRequestIntent(is_image_request=True)
    visual=VisualIntent(body_or_face_regions=['genital_area'], nudity_level='full_nudity', explicit_anatomy_focus=True)
    apply_semantic_safety_contract(intent, visual, {'explicit_genital_visibility': True})
    assert intent.content_classification == v2.ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
    assert intent.body_visibility.regions['genitals'].visibility_requested is True
    decision=v2.evaluate_safety_policy(intent, v2.AdultImagePolicyContext(adult_enabled=True, adult_addon_owned=True, adult_addon_enabled=True, fictional_partner_min_age=21))
    assert decision.decision == v2.PolicyDecision.DENY
    assert decision.reason_code == 'explicit_genital_visibility_not_supported'


def test_full_nudity_without_scene_cannot_inherit_public_routine():
    intent=v2.ImageRequestIntent(is_image_request=True, content_classification=v2.ContentClassification.FULL_NUDITY)
    result=apply_adult_scene_policy(intent, {'location':'street', 'slot_name':'evening'})
    assert result.denied_reason is None
    assert result.private_scene_applied is True
    assert result.routine_context['location'] is None
    assert intent.scene.privacy == 'private'
    assert intent.scene.environment_type == 'private_indoor'


def test_explicit_private_scene_is_preserved_and_public_scene_is_denied():
    private=v2.ImageRequestIntent(is_image_request=True, content_classification=v2.ContentClassification.FULL_NUDITY)
    private.scene=v2.SceneIntent(scene_key='bathroom', location='bathroom', environment_type='private_indoor', privacy='private', explicit_current_request=True)
    result=apply_adult_scene_policy(private, {'location':'cafe'})
    assert result.private_scene_applied is False
    assert private.scene.scene_key == 'bathroom'

    public=v2.ImageRequestIntent(is_image_request=True, content_classification=v2.ContentClassification.FULL_NUDITY)
    public.scene=v2.SceneIntent(scene_key='street', location='street', environment_type='public_outdoor', privacy='public', explicit_current_request=True)
    result=apply_adult_scene_policy(public, {'location':'home'})
    assert result.denied_reason == 'adult_public_scene_not_supported'


def test_adult_model_selection_is_conditional():
    assert select_generation_model(content_classification=v2.ContentClassification.NORMAL, default_model='krea', adult_model='lustify') == 'krea'
    assert select_generation_model(content_classification=v2.ContentClassification.FULL_NUDITY, default_model='krea', adult_model='lustify') == 'lustify'


def test_adult_anatomy_consensus_fails_on_any_structural_disagreement():
    profile_pass=_qa(model='vision-primary')
    structure_fail=_qa(passed=False, model='vision-fallback', implausible_anatomy=True, reason_codes=['implausible_anatomy'])
    result=merge_adult_anatomy_qa_results([profile_pass, structure_fail])
    assert result.passed is False
    assert result.consensus_passed is False
    assert 'implausible_anatomy' in result.reason_codes


def test_adult_anatomy_consensus_requires_two_independent_passes():
    incomplete=merge_adult_anatomy_qa_results([_qa()])
    assert incomplete.passed is False
    assert 'anatomy_qa_consensus_incomplete' in incomplete.reason_codes
    complete=merge_adult_anatomy_qa_results([_qa(model='a'), _qa(model='b')])
    assert complete.passed is True
    assert complete.consensus_passed is True
    assert len(complete.qa_passes) == 2
