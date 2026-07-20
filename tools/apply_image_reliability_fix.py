from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}, found {count}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1))


def replace_at_least_once(path: str, old: str, new: str, *, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text()
    found = text.count(old)
    if found < count:
        raise RuntimeError(f"expected at least {count} matches in {path}, found {found}: {old[:120]!r}")
    p.write_text(text.replace(old, new, count))


GUARDRAILS = '''from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_BODY_REGION_ALIASES = {
    "genitals": "genitals",
    "genital": "genitals",
    "genital_area": "genitals",
    "sexual_organs": "genitals",
    "intimate_anatomy": "genitals",
    "penis": "genitals",
    "vulva": "genitals",
    "chest": "chest",
    "breasts": "chest",
    "full_body": "full_body",
    "face": "face",
}

_PUBLIC_PRIVACY_VALUES = {"public", "public_outdoor", "public_indoor", "street", "cafe", "park"}


@dataclass(frozen=True)
class AdultScenePolicyResult:
    routine_context: dict[str, Any] | None
    private_scene_applied: bool = False
    denied_reason: str | None = None


def canonical_body_region(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _BODY_REGION_ALIASES.get(normalized, normalized)


def apply_semantic_safety_contract(intent, visual_intent, safety_signals: dict[str, Any] | None = None):
    """Transfer model-extracted safety fields into the validated V2 intent.

    This is deliberately based on structured semantic fields, not Persian keyword matching.
    """
    from app.services import image_pipeline_v2 as v2

    signals = safety_signals or {}
    canonical_regions: list[str] = []
    for raw_region in list(getattr(visual_intent, "body_or_face_regions", []) or []):
        region = canonical_body_region(raw_region)
        if not region:
            continue
        canonical_regions.append(region)
        current = intent.body_visibility.regions.get(region)
        if current is None:
            current = v2.BodyRegionIntent()
            intent.body_visibility.regions[region] = current
        current.mentioned = True
        current.visibility_requested = True
        current.framing_requested = True
        current.explicit_current_request = True

    explicit_focus = bool(
        getattr(visual_intent, "explicit_anatomy_focus", False)
        or signals.get("explicit_anatomy_focus")
        or signals.get("explicit_genital_visibility")
        or "genitals" in canonical_regions
    )
    nudity_level = str(
        getattr(visual_intent, "nudity_level", None)
        or signals.get("nudity_level")
        or ""
    ).strip().lower()

    if explicit_focus:
        intent.content_classification = v2.ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
        intent.adult_intent = "explicit_genital_visibility"
        region = intent.body_visibility.regions.setdefault("genitals", v2.BodyRegionIntent())
        region.mentioned = True
        region.visibility_requested = True
        region.framing_requested = True
        region.explicit_current_request = True
    elif nudity_level in {"full_nudity", "nude", "fully_nude"}:
        intent.content_classification = v2.ContentClassification.FULL_NUDITY
        intent.adult_intent = "full_nudity"
    elif nudity_level == "topless":
        intent.content_classification = v2.ContentClassification.TOPLESS
        intent.adult_intent = "topless"
    elif nudity_level == "lingerie":
        intent.content_classification = v2.ContentClassification.LINGERIE
        intent.adult_intent = "lingerie"
    elif nudity_level == "suggestive":
        intent.content_classification = v2.ContentClassification.SUGGESTIVE
        intent.adult_intent = "suggestive"
    return intent


def apply_adult_scene_policy(intent, routine_context: dict[str, Any] | None) -> AdultScenePolicyResult:
    """Keep allowed full nudity in a private setting unless the user explicitly chose one.

    The policy never invents furniture, pose, clothing, or lighting. It only prevents routine
    context (for example a street or cafe) from turning a context-free nude request into public nudity.
    """
    from app.services import image_pipeline_v2 as v2

    if str(intent.content_classification) != str(v2.ContentClassification.FULL_NUDITY):
        return AdultScenePolicyResult(routine_context=routine_context)

    explicit_scene = bool(
        intent.scene.explicit_current_request
        and (intent.scene.scene_key or intent.scene.location or intent.scene.environment_type)
    )
    privacy = str(intent.scene.privacy or "").strip().lower()
    environment = str(intent.scene.environment_type or "").strip().lower()

    if explicit_scene and (privacy in _PUBLIC_PRIVACY_VALUES or environment in _PUBLIC_PRIVACY_VALUES):
        return AdultScenePolicyResult(routine_context=routine_context, denied_reason="adult_public_scene_not_supported")

    if explicit_scene:
        return AdultScenePolicyResult(routine_context=routine_context)

    intent.scene.scene_key = "private_indoor"
    intent.scene.location = "private indoor setting"
    intent.scene.environment_type = "private_indoor"
    intent.scene.privacy = "private"
    intent.scene.required_visible_environment_elements = ["private indoor environment"]
    safe_routine = dict(routine_context or {})
    safe_routine["location"] = None
    return AdultScenePolicyResult(routine_context=safe_routine, private_scene_applied=True)


def select_generation_model(*, content_classification: object, default_model: str, adult_model: str | None) -> str:
    from app.services import image_pipeline_v2 as v2

    if str(content_classification) == str(v2.ContentClassification.FULL_NUDITY) and str(adult_model or "").strip():
        return str(adult_model).strip()
    return default_model
'''

Path("app/services/image_generation_guardrails.py").write_text(GUARDRAILS)

# Semantic router: make safety-critical visual fields explicit in the model contract.
replace_once(
    "app/services/semantic_image_intent_router.py",
    "    gaze_direction: str | None = None\n    eye_contact_required: bool = False\n",
    "    gaze_direction: str | None = None\n    eye_contact_required: bool = False\n    nudity_level: str | None = None\n    explicit_anatomy_focus: bool = False\n",
)
replace_once(
    "app/services/semantic_image_intent_router.py",
    '            "Return ONLY valid JSON matching the provided schema. Do not include prose. "\n',
    '            "For adult visual requests, set visual_intent.nudity_level to normal, suggestive, lingerie, topless, or full_nudity. If the user explicitly asks to see, focus on, or frame genital/sexual anatomy, set visual_intent.explicit_anatomy_focus=true, include the canonical body_or_face_regions value genitals, and set safety_relevant_signals.explicit_genital_visibility=true. Never hide a safety-critical anatomical focus in freeform text. "\n            "Return ONLY valid JSON matching the provided schema. Do not include prose. "\n',
)

# Runtime configuration: adult generation can use a purpose-built model while normal images remain unchanged.
replace_once(
    "app/core/config.py",
    '    image_generation_fallback_model: str = "seedream-v5-lite"\n',
    '    image_generation_fallback_model: str = "seedream-v5-lite"\n    image_generation_adult_model: str = "lustify-sdxl"\n',
)

# Image generation service wiring.
replace_once(
    "app/services/image_generation_service.py",
    "from app.services.image_request_state_machine import begin_or_update_chain, is_duplicate_command, mark_state, metadata_for_chain, ImageRequestState, sync_image_request_chain_state\n",
    "from app.services.image_request_state_machine import begin_or_update_chain, is_duplicate_command, mark_state, metadata_for_chain, ImageRequestState, sync_image_request_chain_state\nfrom app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_adult_scene_policy, select_generation_model\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "    for region in getattr(vi, 'body_or_face_regions', []) or []:\n        if region: intent.body_visibility.regions.setdefault(region, v2.BodyRegionIntent(mentioned=True, explicit_current_request=True))\n",
    "    intent=apply_semantic_safety_contract(intent, vi, getattr(semantic_decision, 'safety_relevant_signals', None) if semantic_decision is not None else None)\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "def image_generation_quote(db: Session):\n    pricing=CoinPricingService(); img=get_price('venice', DEFAULT_IMAGE_MODEL, image_resolution_tier(DEFAULT_WIDTH, DEFAULT_HEIGHT))\n",
    "def image_generation_quote(db: Session, model: str=DEFAULT_IMAGE_MODEL):\n    pricing=CoinPricingService(); img=get_price('venice', model, image_resolution_tier(DEFAULT_WIDTH, DEFAULT_HEIGHT))\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "    image=pricing.quote_usd(db, img.standard_rate_usd, {'feature':'image_generation','model':DEFAULT_IMAGE_MODEL,'resolution':'1024x1280','tier':image_resolution_tier(DEFAULT_WIDTH,DEFAULT_HEIGHT)})\n",
    "    image=pricing.quote_usd(db, img.standard_rate_usd, {'feature':'image_generation','model':model,'resolution':'1024x1280','tier':image_resolution_tier(DEFAULT_WIDTH,DEFAULT_HEIGHT)})\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "    policy_context=v2.AdultImagePolicyContext(adult_enabled=adult_global, soft_safety_enabled=soft_safety, normal_addon_owned=user_has_addon(db, user.id, IMAGE_ADDON_KEY), normal_addon_enabled=user_addon_enabled(db, user.id, IMAGE_ADDON_KEY), adult_addon_owned=user_owns_addon(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK), adult_addon_enabled=user_addon_enabled(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK), fictional_partner_min_age=getattr(user, 'fictional_partner_age', None) or getattr(user, 'fictional_age', None) or 18, parsed_body_visibility={k:v.__dict__ for k,v in intent.body_visibility.regions.items()}, nudity_level=str(intent.content_classification))\n",
    "    scene_policy=apply_adult_scene_policy(intent, routine_slot)\n    if scene_policy.denied_reason:\n        raise ImageGenerationDenied(scene_policy.denied_reason)\n    routine_slot=scene_policy.routine_context or {}\n    policy_context=v2.AdultImagePolicyContext(adult_enabled=adult_global, soft_safety_enabled=soft_safety, normal_addon_owned=user_has_addon(db, user.id, IMAGE_ADDON_KEY), normal_addon_enabled=user_addon_enabled(db, user.id, IMAGE_ADDON_KEY), adult_addon_owned=user_owns_addon(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK), adult_addon_enabled=user_addon_enabled(db, user.id, ADULT_IMAGE_GENERATION_UNLOCK), fictional_partner_min_age=getattr(user, 'fictional_partner_age', None) or getattr(user, 'fictional_age', None) or 18, parsed_body_visibility={k:v.__dict__ for k,v in intent.body_visibility.regions.items()}, nudity_level=str(intent.content_classification))\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "    plan=v2.construct_resolved_plan(intent, merged, safety, profile, source_job=source_job, message_id=source_telegram_message_id, user_request=user_request)\n",
    "    plan=v2.construct_resolved_plan(intent, merged, safety, profile, source_job=source_job, message_id=source_telegram_message_id, user_request=user_request)\n    if scene_policy.private_scene_applied:\n        plan.visual_requirements.environment_visibility_required=True\n        plan.visual_requirements.visibility_targets.environment_visible=True\n        plan.visual_requirements.must_satisfy['required_scene_elements']=['private_indoor', 'private indoor setting']\n        plan.visual_requirements.reason_codes.append('adult_private_scene_required')\n    runtime_settings=get_settings()\n    generation_model=select_generation_model(content_classification=intent.content_classification, default_model=DEFAULT_IMAGE_MODEL, adult_model=getattr(runtime_settings, 'image_generation_adult_model', None))\n    plan.provider_capability_decision.model=generation_model\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "    quote=image_generation_quote(db); correlation=new_correlation_id('image')\n",
    "    quote=image_generation_quote(db, generation_model); correlation=new_correlation_id('image')\n",
)
replace_once(
    "app/services/image_generation_service.py",
    "provider='venice',model=DEFAULT_IMAGE_MODEL,quote=quote",
    "provider='venice',model=generation_model,quote=quote",
)
replace_once(
    "app/services/image_generation_service.py",
    "'provider_capabilities':v2.ProviderImageCapabilities().__dict__",
    "'provider_capabilities':v2.ProviderImageCapabilities().__dict__,'selected_generation_model':generation_model,'adult_private_scene_policy_applied':scene_policy.private_scene_applied",
)
replace_once(
    "app/services/image_generation_service.py",
    "}, model=DEFAULT_IMAGE_MODEL, width=compiled.provider_parameters['width']",
    "}, model=generation_model, width=compiled.provider_parameters['width']",
)

# Adult anatomy QA: independent profile and structural reviews, fail-closed consensus.
replace_once(
    "app/services/generated_image_qa_service.py",
    "'ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure'\n",
    "'ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure','anatomy_qa_consensus_incomplete','anatomy_qa_disagreement'\n",
)
replace_once(
    "app/services/generated_image_qa_service.py",
    "        if hasattr(self, 'raw_provider_reason_codes'):\n            data['raw_provider_reason_codes']=getattr(self, 'raw_provider_reason_codes')\n        return data\n",
    "        if hasattr(self, 'raw_provider_reason_codes'):\n            data['raw_provider_reason_codes']=getattr(self, 'raw_provider_reason_codes')\n        if hasattr(self, 'qa_passes'):\n            data['qa_passes']=getattr(self, 'qa_passes')\n        if hasattr(self, 'consensus_passed'):\n            data['consensus_passed']=getattr(self, 'consensus_passed')\n        return data\n",
)
old_qa_block = '''ADULT_ANATOMY_QA_PROMPT="""You are a high-level adult anatomy consistency QA module for fictional adults. Return JSON only. Do not describe genitalia or sexual details. Assess only whether visible anatomy is consistent with the stored anatomical_profile enum supplied in requirements. Schema: {"anatomy_visible_enough_to_assess":true,"anatomy_consistent_with_profile":true,"contradictory_sex_characteristics":false,"malformed_anatomy":false,"implausible_anatomy":false,"duplicated_anatomy_parts":false,"missing_expected_parts_when_visible":false,"ambiguous_anatomy":false,"confidence":"high","reason_codes":[]}. Separate identity/profile consistency from anatomical plausibility/structural quality. Reject visible malformed, duplicated, missing, merged, misplaced, implausibly shaped, or obviously synthetic broken adult anatomy using booleans and reason_codes only; do not store graphic descriptions."""
'''
new_qa_block = '''ADULT_ANATOMY_QA_SCHEMA=''' + "'''" + '''{"anatomy_visible_enough_to_assess":true,"anatomy_consistent_with_profile":true,"contradictory_sex_characteristics":false,"malformed_anatomy":false,"implausible_anatomy":false,"duplicated_anatomy_parts":false,"missing_expected_parts_when_visible":false,"ambiguous_anatomy":false,"confidence":"high","reason_codes":[]}''' + "'''" + '''
ADULT_ANATOMY_PROFILE_QA_PROMPT="""You are pass 1 of a fail-closed QA system for fictional adult images. Return JSON only using the supplied schema. Do not describe intimate anatomy. Verify that visible sex characteristics are internally consistent with the stored anatomical_profile. Mark uncertain or not assessable instead of guessing. A pass requires medium/high confidence and no contradiction, ambiguity, or malformed structure."""
ADULT_ANATOMY_STRUCTURE_QA_PROMPT="""You are pass 2 of a fail-closed QA system for fictional adult images. Return JSON only using the supplied schema. Do not describe intimate anatomy. Independently inspect structural plausibility: reject merged, misplaced, duplicated, missing, ambiguous, implausibly shaped, or obviously synthetic broken anatomy. Do not defer to the first reviewer. Mark uncertain when the image cannot be assessed reliably."""
'''
replace_once("app/services/generated_image_qa_service.py", old_qa_block, new_qa_block)
old_eval = '''async def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        logger.warning('ADULT_ANATOMY_QA_FAILED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, 'low', ['anatomy_qa_provider_failure'])
        return evaluate_adult_anatomy_payload({'reason_codes':['anatomy_qa_provider_failure']}, anatomical_profile=anatomical_profile)
    prompt=ADULT_ANATOMY_QA_PROMPT + "\\nRequirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)
    logger.info('ADULT_ANATOMY_QA_STARTED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, None, [])
    try:
        payload=await analyze_image_bytes_with_venice(image_bytes, prompt=prompt, model=settings.vision_model)
        result=evaluate_adult_anatomy_payload(payload, anatomical_profile=anatomical_profile, model=settings.vision_model)
    except Exception:
        result=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],None)
    logger.info('ADULT_ANATOMY_QA_COMPLETED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    logger.info('ADULT_ANATOMY_QA_%s user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', 'PASSED' if result.passed else 'FAILED', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    return result
'''
new_eval = '''def merge_adult_anatomy_qa_results(results: list[GeneratedImageQAResult]) -> GeneratedImageQAResult:
    if len(results) < 2:
        failure=GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_consensus_incomplete'],None)
        failure.consensus_passed=False
        failure.qa_passes=[]
        return failure
    codes=list(dict.fromkeys(code for result in results for code in (result.reason_codes or [])))
    passes=all(result.passed and result.confidence in {'medium','high'} for result in results)
    visible=all(result.anatomy_visible_enough_to_assess is True for result in results)
    consistent=all(result.anatomy_consistent_with_profile is True for result in results)
    contradictory=any(result.contradictory_sex_characteristics is True for result in results)
    malformed=any(result.malformed_anatomy is True for result in results)
    implausible=any(result.implausible_anatomy is True for result in results)
    duplicated=any(result.duplicated_anatomy_parts is True for result in results)
    missing=any(result.missing_expected_parts_when_visible is True for result in results)
    ambiguous=any(result.ambiguous_anatomy is True for result in results)
    if not (passes and visible and consistent and not any([contradictory, malformed, implausible, duplicated, missing, ambiguous])):
        if not codes:
            codes.append('anatomy_qa_disagreement')
        passes=False
    confidence='high' if passes and all(r.confidence == 'high' for r in results) else ('medium' if passes else 'low')
    merged=GeneratedImageQAResult(passes,None,None,False,False,False,False,False,False,confidence,codes,'consensus:' + '+'.join(str(r.model or 'unknown') for r in results), anatomy_visible_enough_to_assess=visible, anatomy_consistent_with_profile=consistent, contradictory_sex_characteristics=contradictory, malformed_anatomy=malformed, implausible_anatomy=implausible, duplicated_anatomy_parts=duplicated, missing_expected_parts_when_visible=missing, ambiguous_anatomy=ambiguous)
    merged.consensus_passed=passes
    merged.qa_passes=[{'model':r.model,'passed':r.passed,'confidence':r.confidence,'reason_codes':list(r.reason_codes or [])} for r in results]
    return merged

async def evaluate_adult_anatomy_image(image_bytes: bytes, *, anatomical_profile: str, user_id=None, job_id=None, request_chain_id=None) -> GeneratedImageQAResult:
    settings=get_settings()
    if not getattr(settings, 'venice_api_key', ''):
        logger.warning('ADULT_ANATOMY_QA_FAILED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, 'low', ['anatomy_qa_provider_failure'])
        return merge_adult_anatomy_qa_results([])
    fallback=getattr(settings, 'vision_fallback_model', None) or settings.vision_model
    review_plan=[(settings.vision_model, ADULT_ANATOMY_PROFILE_QA_PROMPT), (fallback, ADULT_ANATOMY_STRUCTURE_QA_PROMPT)]
    logger.info('ADULT_ANATOMY_QA_STARTED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, None, [])
    results=[]
    for model, review_prompt in review_plan:
        prompt=review_prompt + "\\nSchema: " + ADULT_ANATOMY_QA_SCHEMA + "\\nRequirements: " + json.dumps({'anatomical_profile': anatomical_profile}, sort_keys=True)
        try:
            payload=await analyze_image_bytes_with_venice(image_bytes, prompt=prompt, model=model)
            results.append(evaluate_adult_anatomy_payload(payload, anatomical_profile=anatomical_profile, model=model))
        except Exception:
            results.append(GeneratedImageQAResult(False,None,None,False,False,False,False,False,False,'low',['anatomy_qa_provider_failure'],model))
    result=merge_adult_anatomy_qa_results(results)
    logger.info('ADULT_ANATOMY_QA_COMPLETED user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    logger.info('ADULT_ANATOMY_QA_%s user_id=%s job_id=%s request_chain_id=%s anatomical_profile=%s confidence=%s reason_codes=%s', 'PASSED' if result.passed else 'FAILED', user_id, job_id, request_chain_id, anatomical_profile, result.confidence, result.reason_codes)
    return result
'''
replace_once("app/services/generated_image_qa_service.py", old_eval, new_eval)
replace_once(
    "app/services/generated_image_qa_service.py",
    "and aqa.get('passed') is True and aqa.get('artifact_checksum')",
    "and aqa.get('passed') is True and aqa.get('consensus_passed') is True and len(aqa.get('qa_passes') or []) >= 2 and aqa.get('artifact_checksum')",
)
replace_once(
    "app/services/generated_image_qa_service.py",
    "'ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure'}:\n",
    "'ambiguous_anatomy','anatomy_not_assessable','anatomy_qa_provider_failure','anatomy_qa_consensus_incomplete','anatomy_qa_disagreement'}:\n",
)

TESTS = '''from types import SimpleNamespace

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
'''
Path("tests/test_image_generation_reliability.py").write_text(TESTS)

print("image reliability changes applied")
