from __future__ import annotations

from pathlib import Path
import re


def replace_once(path: str, old: str, new: str) -> None:
    p=Path(path); text=p.read_text(encoding='utf-8'); count=text.count(old)
    if count == 0:
        if new in text:
            print('already applied', path, old[:80]); return
        raise RuntimeError(f'target not found in {path}: {old[:160]!r}')
    if count != 1:
        raise RuntimeError(f'expected one target in {path}, got {count}: {old[:120]!r}')
    p.write_text(text.replace(old,new,1),encoding='utf-8')


def regex_once(path: str, pattern: str, replacement: str) -> None:
    p=Path(path); text=p.read_text(encoding='utf-8')
    new,count=re.subn(pattern,replacement,text,count=1,flags=re.S|re.M)
    if count == 0:
        if replacement.strip() in text:
            print('already applied regex', path); return
        raise RuntimeError(f'regex target not found in {path}: {pattern[:140]!r}')
    p.write_text(new,encoding='utf-8')


# ---------------------------------------------------------------------------
# Semantic image router: preserve detailed requests for the LLM and expand the
# structured contract instead of collapsing every clear request to an empty
# deterministic generate_new decision.
# ---------------------------------------------------------------------------
replace_once(
    'app/services/semantic_image_intent_router.py',
    'SEMANTIC_ROUTER_SCHEMA_VERSION = "semantic-image-intent-v1"',
    'SEMANTIC_ROUTER_SCHEMA_VERSION = "semantic-image-intent-v2-partner-photo"',
)
replace_once(
    'app/services/semantic_image_intent_router.py',
    '''    nudity_level: str | None = None\n    explicit_anatomy_focus: bool = False\n''',
    '''    nudity_level: str | None = None\n    explicit_anatomy_focus: bool = False\n    request_type: str | None = None\n    primary_subject: str | None = None\n    partner_visible: bool | None = None\n    pet_visible: bool = False\n    object_only: bool = False\n    pet_only: bool = False\n    hands_only: bool = False\n    face_visible: bool | None = None\n    face_hidden: bool = False\n    back_to_camera: bool = False\n    camera_mode: str | None = None\n    required_body_regions: list[str] = field(default_factory=list)\n    forbidden_body_regions: list[str] = field(default_factory=list)\n    realism_constraints: list[str] = field(default_factory=list)\n    natural_capture_required: bool = True\n''',
)
regex_once(
    'app/services/semantic_image_intent_router.py',
    r'''    wants_visual = "عکس" in t or "تصویر" in t or "ببینمت" in t or "نشونم بده" in t\n    delivery = any\(v in t for v in \["بده", "بدی", "بفرست", "بفرستی", "بساز", "درست کن", "ببینمت", "نشونم بده", "بشین", "باشی"\]\)\n    has_structured_visual_constraint = any\(v in t for v in \["مبل", "خونه", "خانه", "نشسته", "بشین", "کت شلوار", "کت مشکی", "لباس"\]\)\n    if \(wants_visual and delivery\) or \(has_structured_visual_constraint and delivery\):\n        logger\.info\([^\n]+\)\n        return SemanticImageAction\.GENERATE_NEW\n    return None''',
    '''    # Detailed media requests must reach the semantic model so scene, camera,\n    # subject visibility, pet/object focus, pose and adult intent are not discarded.\n    # Only exact control/clarification commands above are handled deterministically.\n    return None''',
)
replace_once(
    'app/services/semantic_image_intent_router.py',
    '''        if decision.confidence < threshold and decision.action != SemanticImageAction.CHAT:\n''',
    '''        if (decision.confidence < threshold and decision.action != SemanticImageAction.CHAT\n                and not (decision.action == SemanticImageAction.GENERATE_NEW and decision.media_delivery_requested)):\n''',
)
replace_once(
    'app/services/semantic_image_intent_router.py',
    '''            "For adult visual requests, set visual_intent.nudity_level to normal, suggestive, lingerie, topless, or full_nudity. If the user explicitly asks to see, focus on, or frame genital/sexual anatomy, set visual_intent.explicit_anatomy_focus=true, include the canonical body_or_face_regions value genitals, and set safety_relevant_signals.explicit_genital_visibility=true. Never hide a safety-critical anatomical focus in freeform text. "\n''',
    '''            "Never choose clarify for a straightforward request to send a photo, including ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, tripod, driving, cafe, bedroom, bathroom, nature, city, or car requests. Choose generate_new and extract the best complete visual contract. Clarify only when it is genuinely impossible to know whether the user wants a new image, a modification of an existing image, or chat. "\n            "Populate visual_intent.request_type and visual_intent.primary_subject (partner, pet, object, or scene). Set partner_visible, pet_visible, object_only, pet_only, hands_only, face_visible, face_hidden, and back_to_camera explicitly when requested or clearly implied. Set visual_intent.camera_mode to casual_selfie, mirror_selfie, tripod_timer, point_of_view, passenger_pov, dashboard_mount, candid, or casual_phone_photo. A full-body selfie should normally be mirror_selfie unless the user explicitly describes a timer/tripod. For coffee, food, personal-object, or pet photos, allow the partner to be absent and do not force a portrait. For hands-only requests, set hands_only=true, face_hidden=true, include hands in required_body_regions, and use point_of_view unless another camera method is explicit. For a back-facing request set back_to_camera=true and do not silently turn the subject into a front-facing portrait. Always set natural_capture_required=true unless the user explicitly requests a studio/editorial image. "\n            "The photo must behave like a real partner taking or sharing a plausible personal photo: preserve requested camera logic, avoid passport/headshot defaults, avoid impossible self-photography while driving, and use the recent conversation for current place/activity without overriding explicit current instructions. Extract visible_objects and held_objects precisely. "\n            "For adult visual requests, set visual_intent.nudity_level to normal, suggestive, lingerie, topless, or full_nudity. If the user explicitly asks to see, focus on, or frame genital/sexual anatomy, set visual_intent.explicit_anatomy_focus=true, include the canonical body_or_face_regions value genitals, and set safety_relevant_signals.explicit_genital_visibility=true. Never hide a safety-critical anatomical focus in freeform text. "\n''',
)

# ---------------------------------------------------------------------------
# Pipeline data structures and shared photo contract.
# ---------------------------------------------------------------------------
replace_once(
    'app/services/image_pipeline_v2.py',
    'from app.services.image_semantic_lexicons import IMAGE_SEMANTIC_LEXICONS\n',
    'from app.services.image_semantic_lexicons import IMAGE_SEMANTIC_LEXICONS\nfrom app.services.partner_photo_contract import prompt_constraints\n',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    is_image_request: bool=False; route: ImageRouteDecisionV2|None=None; parse_coverage: ParseCoverage=field(default_factory=ParseCoverage); adult_intent: str|None=None; content_classification: str=ContentClassification.NORMAL; body_visibility: BodyVisibilityIntent=field(default_factory=BodyVisibilityIntent); scene: SceneIntent=field(default_factory=SceneIntent); pose: PoseIntent=field(default_factory=PoseIntent); wardrobe: WardrobeIntent=field(default_factory=WardrobeIntent); composition: CompositionIntent=field(default_factory=CompositionIntent); continuity: ContinuityIntent=field(default_factory=ContinuityIntent); identity: IdentityIntent=field(default_factory=IdentityIntent); visual_assertions: list[VisualAssertion]=field(default_factory=list); expression_modifiers: list[ExpressionModifier]=field(default_factory=list); explicit_exclusions: list[str]=field(default_factory=list); secondary_subject: SecondarySubjectIntent=field(default_factory=SecondarySubjectIntent); interaction: str|None=None; passthrough_visual_details: list[str]=field(default_factory=list); gaze_direction: str|None=None; eye_contact_required: bool=False\n''',
    '''    is_image_request: bool=False; route: ImageRouteDecisionV2|None=None; parse_coverage: ParseCoverage=field(default_factory=ParseCoverage); adult_intent: str|None=None; content_classification: str=ContentClassification.NORMAL; body_visibility: BodyVisibilityIntent=field(default_factory=BodyVisibilityIntent); scene: SceneIntent=field(default_factory=SceneIntent); pose: PoseIntent=field(default_factory=PoseIntent); wardrobe: WardrobeIntent=field(default_factory=WardrobeIntent); composition: CompositionIntent=field(default_factory=CompositionIntent); continuity: ContinuityIntent=field(default_factory=ContinuityIntent); identity: IdentityIntent=field(default_factory=IdentityIntent); visual_assertions: list[VisualAssertion]=field(default_factory=list); expression_modifiers: list[ExpressionModifier]=field(default_factory=list); explicit_exclusions: list[str]=field(default_factory=list); secondary_subject: SecondarySubjectIntent=field(default_factory=SecondarySubjectIntent); interaction: str|None=None; passthrough_visual_details: list[str]=field(default_factory=list); gaze_direction: str|None=None; eye_contact_required: bool=False; expected_subject_count: int|None=None; photo_contract: dict=field(default_factory=dict)\n''',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    face_visible: bool=True; upper_body_visible: bool=False; full_outfit_visible: bool=False; hands_visible: bool=False; held_object_visible: bool=False; environment_visible: bool=False\n''',
    '''    face_visible: bool=True; face_hidden: bool=False; upper_body_visible: bool=False; full_outfit_visible: bool=False; hands_visible: bool=False; held_object_visible: bool=False; environment_visible: bool=False; partner_visible: bool=True; pet_visible: bool=False; object_only: bool=False; back_view: bool=False\n''',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    requested_action: str=ImageAction.NEW_GENERATION; anatomical_profile: str|None=None; anatomy_consistency_required: bool=False; anatomy_source: str|None=None; explicit_nudity_requested: bool=False; anatomy_qa_required: bool=False; full_body_visible: bool=False; head_visible: bool=False; feet_visible: bool=False; body_not_cropped: bool=False; visibility_targets: VisibilityTargets=field(default_factory=VisibilityTargets); style_targets: StyleTargets=field(default_factory=StyleTargets); continuity_targets: ContinuityTargets=field(default_factory=ContinuityTargets); wardrobe_requested: bool=False; wardrobe_visibility_required: bool=False; environment_visibility_required: bool=False; must_satisfy: dict=field(default_factory=dict); forbidden_regressions: list[str]=field(default_factory=list); framing_requirement: str='medium'; correction_signals: list[str]=field(default_factory=list); reason_codes: list[str]=field(default_factory=list); gaze_direction: str|None=None; eye_contact_required: bool=False\n''',
    '''    requested_action: str=ImageAction.NEW_GENERATION; anatomical_profile: str|None=None; anatomy_consistency_required: bool=False; anatomy_source: str|None=None; explicit_nudity_requested: bool=False; anatomy_qa_required: bool=False; full_body_visible: bool=False; head_visible: bool=False; feet_visible: bool=False; body_not_cropped: bool=False; visibility_targets: VisibilityTargets=field(default_factory=VisibilityTargets); style_targets: StyleTargets=field(default_factory=StyleTargets); continuity_targets: ContinuityTargets=field(default_factory=ContinuityTargets); wardrobe_requested: bool=False; wardrobe_visibility_required: bool=False; environment_visibility_required: bool=False; must_satisfy: dict=field(default_factory=dict); forbidden_regressions: list[str]=field(default_factory=list); framing_requirement: str='medium'; correction_signals: list[str]=field(default_factory=list); reason_codes: list[str]=field(default_factory=list); gaze_direction: str|None=None; eye_contact_required: bool=False; primary_subject: str='partner'; partner_visible: bool=True; pet_visible: bool=False; object_only: bool=False; pet_only: bool=False; hands_only: bool=False; face_visible_required: bool|None=None; face_hidden_required: bool=False; back_to_camera_required: bool=False; camera_mode: str|None=None; natural_capture_required: bool=True; identity_visibility_scope: str='full'; required_body_regions: list[str]=field(default_factory=list); forbidden_body_regions: list[str]=field(default_factory=list); required_objects: list[str]=field(default_factory=list); world_memory_context: list[str]=field(default_factory=list); photo_contract: dict=field(default_factory=dict)\n''',
)

new_resolve = r'''def resolve_visual_requirements(intent: ImageRequestIntent, *, user_request: str='', previous_job=None) -> VisualRequirements:
    action=canonical_image_action(intent.continuity.action)
    text=user_request or ''
    contract=dict(getattr(intent, 'photo_contract', {}) or {})
    wardrobe=intent.wardrobe.wardrobe or _wardrobe_from_text(text)
    critique=extract_visual_critique(text)
    camera_mode=contract.get('camera_mode') or intent.composition.camera or 'casual_phone_photo'
    requested_framing=contract.get('framing') or intent.composition.framing
    vr=VisualRequirements(
        requested_action=action,
        style_targets=StyleTargets(
            wardrobe=wardrobe,
            expression=','.join(e.value for e in intent.expression_modifiers) or None,
            realism_constraints=list(contract.get('realism_constraints') or []),
        ),
        correction_signals=critique,
        primary_subject=contract.get('primary_subject') or 'partner',
        partner_visible=bool(contract.get('partner_visible', True)),
        pet_visible=bool(contract.get('pet_visible')),
        object_only=bool(contract.get('object_only')),
        pet_only=bool(contract.get('pet_only')),
        hands_only=bool(contract.get('hands_only')),
        face_visible_required=contract.get('face_visible'),
        face_hidden_required=bool(contract.get('face_hidden')),
        back_to_camera_required=bool(contract.get('back_to_camera')),
        camera_mode=camera_mode,
        natural_capture_required=bool(contract.get('natural_capture_required', True)),
        identity_visibility_scope=contract.get('identity_visibility_scope') or 'full',
        required_body_regions=list(contract.get('required_body_regions') or []),
        forbidden_body_regions=list(contract.get('forbidden_body_regions') or []),
        required_objects=list(dict.fromkeys((contract.get('visible_objects') or []) + (contract.get('held_objects') or []))),
        world_memory_context=list(contract.get('world_memory_context') or []),
        photo_contract=contract,
    )
    vr.visibility_targets.partner_visible=vr.partner_visible
    vr.visibility_targets.pet_visible=vr.pet_visible
    vr.visibility_targets.object_only=vr.object_only or vr.pet_only
    vr.visibility_targets.hands_visible=vr.hands_only or 'hands' in vr.required_body_regions
    vr.visibility_targets.face_visible=(vr.face_visible_required is not False and not vr.face_hidden_required and vr.partner_visible)
    vr.visibility_targets.face_hidden=vr.face_hidden_required
    vr.visibility_targets.back_view=vr.back_to_camera_required

    semantic_full_body = requested_framing == 'full_body' or intent.composition.framing == 'full_body' or 'full_body' in intent.body_visibility.regions
    if semantic_full_body and vr.partner_visible:
        vr.framing_requirement='full_body'; vr.full_body_visible=True; vr.head_visible=not vr.face_hidden_required; vr.feet_visible=True; vr.body_not_cropped=True; vr.visibility_targets.upper_body_visible=True
        if wardrobe and intent.content_classification != ContentClassification.FULL_NUDITY:
            vr.wardrobe_requested=True; vr.wardrobe_visibility_required=True; vr.visibility_targets.full_outfit_visible=True
        vr.reason_codes.append('full_body_visibility_required')
        vr.must_satisfy.update({'framing':'full_body','full_body_visible':True,'head_visible':vr.head_visible,'feet_visible':True,'body_not_cropped':True,'closeup_forbidden':True,'tight_portrait_forbidden':True})
        logger.info('IMAGE_FULL_BODY_REQUIREMENT_ENFORCED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s', getattr(intent,'user_id',None), getattr(previous_job,'id',None), None, action, 'full_body', 'semantic_full_body')
    elif vr.hands_only or vr.object_only or vr.pet_only or requested_framing == 'detail':
        vr.framing_requirement='detail'
    elif wardrobe:
        vr.wardrobe_requested=True; vr.wardrobe_visibility_required=True; vr.visibility_targets.upper_body_visible=True
        vr.visibility_targets.full_outfit_visible=requested_framing == 'full_body'
        vr.framing_requirement='full_body' if vr.visibility_targets.full_outfit_visible else 'upper_body_or_three_quarter'
        if vr.framing_requirement == 'full_body': vr.full_body_visible=True; vr.head_visible=True; vr.feet_visible=True; vr.body_not_cropped=True
        vr.reason_codes.append('wardrobe_visibility_required')
    elif requested_framing in {'closeup','portrait','close_up'}:
        vr.framing_requirement='closeup_allowed'
    else:
        vr.framing_requirement=requested_framing or 'natural_medium_or_medium_wide'
        vr.visibility_targets.upper_body_visible=vr.partner_visible and not vr.hands_only

    vr.visibility_targets.held_object_visible=bool(vr.required_objects)
    explicit_scene=bool(intent.scene.explicit_current_request and (intent.scene.scene_key or intent.scene.location))
    vr.visibility_targets.environment_visible=bool(intent.scene.scene_key or intent.scene.location or intent.scene.support_surface)
    vr.environment_visibility_required=bool(explicit_scene or intent.scene.support_surface)
    if vr.environment_visibility_required:
        vr.reason_codes.append('environment_visibility_required')
        logger.info('IMAGE_SCENE_REQUIREMENT_ENFORCED user_id=%s job_id=%s request_chain_id=%s action=%s scene=%s location=%s', getattr(intent,'user_id',None), getattr(previous_job,'id',None), None, action, intent.scene.scene_key, intent.scene.location)
    if intent.eye_contact_required and not vr.face_hidden_required:
        vr.gaze_direction='toward_camera'; vr.eye_contact_required=True; vr.must_satisfy['eye_contact_required']=True; vr.reason_codes.append('eye_contact_required')
    if vr.natural_capture_required:
        vr.reason_codes.append('natural_partner_photo_required')
    if vr.face_hidden_required:
        vr.must_satisfy['face_hidden']=True; vr.reason_codes.append('face_hidden_required')
    if vr.face_visible_required is True:
        vr.must_satisfy['face_visible']=True; vr.reason_codes.append('face_visible_required')
    if vr.back_to_camera_required:
        vr.must_satisfy['back_to_camera']=True; vr.reason_codes.append('back_to_camera_required')
    if vr.hands_only:
        vr.must_satisfy['hands_only']=True; vr.reason_codes.append('hands_only_required')

    vr.continuity_targets.preserve_identity=True
    vr.continuity_targets.preserve_previous_scene=action==ImageAction.REFINEMENT
    vr.continuity_targets.preserve_previous_outfit=action==ImageAction.REFINEMENT
    vr.continuity_targets.deliberately_vary_composition=action in {ImageAction.NEW_GENERATION, ImageAction.VARIATION} and previous_job is not None
    if critique and action==ImageAction.NEW_GENERATION and previous_job is not None:
        vr.requested_action=ImageAction.REFINEMENT

    relation_objects=[r.object for r in intent.scene.spatial_relations if getattr(r,'object',None)]
    vr.required_objects=list(dict.fromkeys(vr.required_objects + relation_objects))
    base_must={
        'required_scene_elements': list(dict.fromkeys([x for x in [intent.scene.scene_key, intent.scene.location, *(intent.scene.required_visible_environment_elements or [])] if x])),
        'required_pose_elements': [intent.pose.pose] if intent.pose.pose else [],
        'required_wardrobe_elements': [wardrobe] if wardrobe else [],
        'required_support_surface_elements': [intent.scene.support_surface] if intent.scene.support_surface else [],
        'required_visible_objects': vr.required_objects,
        'required_body_regions': vr.required_body_regions,
        'forbidden_body_regions': vr.forbidden_body_regions,
        'camera_mode': vr.camera_mode,
        'primary_subject': vr.primary_subject,
        'partner_visible': vr.partner_visible,
        'pet_visible': vr.pet_visible,
        'identity_visibility_scope': vr.identity_visibility_scope,
        'natural_capture_required': vr.natural_capture_required,
        'forbidden_regressions': [],
    }
    vr.must_satisfy={**base_must, **(vr.must_satisfy or {})}
    if wardrobe:
        vr.forbidden_regressions.extend(['shirtless','bare_shoulders','casual_sweater_replacement','wardrobe_reset'])
        vr.must_satisfy['forbidden_regressions']=vr.forbidden_regressions
    logger.info('IMAGE_VISUAL_REQUIREMENTS_RESOLVED user_id=%s job_id=%s action=%s continuity_mode=%s reason_codes=%s', getattr(intent,'user_id',None), getattr(previous_job,'id',None), action, action, vr.reason_codes + critique)
    return vr

'''
regex_once('app/services/image_pipeline_v2.py', r'def resolve_visual_requirements\(.*?(?=def plan_continuity)', new_resolve)

replace_once(
    'app/services/image_pipeline_v2.py',
    '''    required=list(dict.fromkeys((list(objs) if scene_key in SCENES else []) + (_objects_for_support(str(surface.value)) if surface.value else [])))\n''',
    '''    contract=dict(getattr(intent, 'photo_contract', {}) or {})\n    relation_objects=[r.object for r in intent.scene.spatial_relations if getattr(r, 'object', None)]\n    required=list(dict.fromkeys((list(objs) if scene_key in SCENES else []) + (_objects_for_support(str(surface.value)) if surface.value else []) + relation_objects + list(contract.get('visible_objects') or []) + list(contract.get('held_objects') or [])))\n''',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    expected_subject_count=2 if intent.secondary_subject.requested or (intent.interaction in {'kiss','hug','holding_hands'} and intent.secondary_subject.role) else 1\n''',
    '''    if getattr(intent, 'expected_subject_count', None) is not None:\n        expected_subject_count=max(0, int(intent.expected_subject_count))\n    elif contract.get('expected_human_subject_count') is not None:\n        expected_subject_count=max(0, int(contract.get('expected_human_subject_count')))\n    else:\n        expected_subject_count=2 if intent.secondary_subject.requested or (intent.interaction in {'kiss','hug','holding_hands'} and intent.secondary_subject.role) else 1\n''',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    ''''all_subjects_fictional_adults': True,'anatomical_profile':visual_requirements.anatomical_profile,''',
    ''''all_subjects_fictional_adults': True,'photo_contract':contract,'anatomical_profile':visual_requirements.anatomical_profile,''',
)

new_compile = r'''def compile_image_prompt(plan: ResolvedImagePlan) -> CompiledImagePrompt:
    desc=plan.identity.get('descriptor',{})
    ident_parts=[f"{k}={v}" for k,v in desc.items() if v not in (None,'',[],{})]
    ident='; '.join(ident_parts)
    content_classification=str(plan.current_intent.get('content_classification') or '').lower()
    allowed_adult_intent=content_classification != 'normal' or bool(plan.body_visibility)
    visibility=', '.join(k for k,v in (plan.body_visibility or {}).items() if v.get('visibility_requested') or v.get('framing_requested'))
    exprs=', '.join(f"{e.get('region') or 'face'} {e.get('value')}" for e in plan.current_intent.get('expression_modifiers', []) if isinstance(e, dict))
    composition=plan.composition or {}
    expected_raw=composition.get('expected_subject_count')
    expected_subject_count=int(expected_raw if expected_raw is not None else 1)
    interaction=composition.get('interaction')
    secondary_role=composition.get('secondary_subject_role')
    contract=dict(composition.get('photo_contract') or getattr(plan.visual_requirements, 'photo_contract', {}) or {})
    partner_visible=bool(contract.get('partner_visible', expected_subject_count > 0))
    sections=[]
    if expected_subject_count == 0:
        subject_contract='Create a photorealistic personal photo with exactly zero visible human people. No face, body, stranger, photographer, or human reflection may appear.'
    elif expected_subject_count == 1:
        subject_contract='Create a realistic image of exactly one fictional adult person matching the stored partner identity. Do not add another person.'
    else:
        subject_contract=f'Create a realistic image of exactly {expected_subject_count} fictional consenting adults matching the resolved identities and roles. Do not add any additional person.'
    sections.append(subject_contract)
    if partner_visible:
        sections.append(f"Subject identity: {ident}.")
        if contract.get('identity_visibility_scope') == 'partial':
            sections.append('Partial identity continuity: preserve all visible identity cues such as skin tone, body build, hands, hair or silhouette without forcing the face into frame.')
        else:
            sections.append('Identity lock: preserve the same recognizable person across requests; keep face shape, eye shape, eyebrow structure, hair style and hairline, skin tone, age appearance, body build and distinguishing details anchored to the stored fingerprint.')
        sections.append('Never change the stored gender presentation or anatomical profile. Do not replace the partner with a generic woman or generic man.')
    else:
        sections.append('The recurring partner is intentionally not visible. Do not invent a substitute person merely to display the stored identity.')

    vr=getattr(plan, 'visual_requirements', VisualRequirements())
    sections.extend(prompt_constraints(contract))
    if vr.must_satisfy:
        sections.append('Must satisfy all requested constraints together: ' + json.dumps(vr.must_satisfy, ensure_ascii=False) + '.')
    if getattr(vr, 'eye_contact_required', False):
        sections.append('Eye contact requirement: subject looking directly toward the camera with visible natural eye contact.')
    if vr.framing_requirement == 'full_body' and partner_visible:
        sections.append('Hard framing requirement: complete full figure visible from head to feet, entire body inside frame, camera far enough to show the whole body, no tight headshot and no crop at torso, knees, or feet.')
        if vr.wardrobe_visibility_required:
            sections.append('Requested wardrobe must be clearly visible and verifiable in the full-body frame.')
    elif vr.framing_requirement == 'detail':
        sections.append('Detail framing requirement: frame the requested hands, object, pet, or body detail naturally; do not fall back to a centered face portrait.')
    elif vr.wardrobe_visibility_required:
        sections.append('Requested wardrobe must be clearly visible and verifiable in an upper-body, three-quarter, or full-body composition.')
    elif vr.framing_requirement == 'natural_medium_or_medium_wide':
        sections.append('Use a natural medium or medium-wide personal-photo composition, not a passport-style centered tight headshot.')
    elif vr.framing_requirement == 'closeup_allowed':
        sections.append('A close composition is allowed because the user explicitly requested it, but it must still look like a real personal photo rather than an ID photo.')

    if plan.action == ImageAction.NEW_GENERATION:
        sections.append('This is a new image, not an exact repeat; preserve identity while varying pose, camera, crop, and scene enough to avoid a near-duplicate.')
    elif plan.action == ImageAction.VARIATION:
        sections.append('This is a deliberate variation: preserve identity and general concept, but meaningfully change composition, camera angle, pose, and scene details.')
    elif plan.action == ImageAction.REFINEMENT:
        sections.append('This is a refinement: preserve identity and relevant previous image features while applying the requested correction.')

    corrections=[]
    for c in vr.correction_signals:
        corrections.append({'under_eye_too_dark':'Reduce heavy under-eye darkness; keep the face clean, healthy, and naturally lit.','outfit_not_visible':'Make the requested outfit clearly visible.','too_close_up':'Pull the camera back; avoid an overly close crop.','not_similar_enough':'Improve identity consistency with the visual profile.','too_artificial':'Use natural realistic skin texture and lighting.','negative_feedback':'Correct the previous quality issue with a cleaner, more satisfying composition.','bad_lighting':'Improve lighting; avoid muddy shadows.','bad_composition':'Improve composition and framing.'}.get(c,c))
    if corrections: sections.append('Correction constraints from user critique: ' + ' '.join(corrections))
    for label, field in [('Scene', plan.scene), ('Location', plan.location), ('Activity', plan.activity), ('Pose', plan.pose), ('Support surface', plan.support_surface), ('Wardrobe', plan.wardrobe), ('Camera mode', plan.camera), ('Lighting', plan.lighting)]:
        rendered=_render_field(label, field)
        if rendered: sections.append(rendered + '.')
    if plan.required_objects.value:
        sections.append('Visible objects: ' + ', '.join(plan.required_objects.value) + '.')
    if exprs: sections.append('Expression/features: ' + exprs + '.')
    if allowed_adult_intent:
        if getattr(vr, 'anatomy_consistency_required', False):
            ap=vr.anatomical_profile
            sections.append(f'Adult anatomy consistency: preserve the stored fictional adult identity. Anatomy must be consistently {ap} according to the stored fictional anatomical profile. No contradictory, mixed, malformed, duplicated, ambiguous, or anatomically impossible structure.')
        body_text=('full nudity, ' + visibility if content_classification.endswith('full_nudity') and visibility else (visibility or ('full nudity with the requested natural framing' if content_classification.endswith('full_nudity') else 'no explicit body emphasis')))
        sections.append('Body visibility: ' + body_text + '.')
    if expected_subject_count == 2:
        interaction_text={'kiss':'mutually kissing with consensual romantic body language','hug':'mutually hugging with consensual affectionate body language','holding_hands':'holding hands with consensual romantic body language'}.get(str(interaction), 'consensual body language')
        sections.append(f"Secondary subject role: one generic fictional adult {secondary_role or 'companion'}, never a real person. Interaction: {interaction_text}.")
    passthrough=[sanitize_passthrough_visual_detail(x) for x in getattr(plan, 'passthrough_visual_details', []) if sanitize_passthrough_visual_detail(x)]
    if passthrough: sections.append('User-requested visual details: ' + '; '.join(passthrough) + '.')
    sections.append('Use a natural, internally consistent, attractive but believable personal-photo composition. Avoid generic AI portrait defaults, plastic skin, studio catalogue posing, and impossible camera geometry.')
    positive=' '.join(sections)
    sec={'identity':ident,'visual_requirements':asdict(vr),'continuity_plan':asdict(getattr(plan,'continuity_plan',ContinuityPlan())),'photo_contract':contract,'passthrough_visual_details':passthrough,'single_subject_contract':subject_contract,'expected_subject_count':expected_subject_count,'interaction':interaction,'secondary_subject_role':secondary_role,'scene':plan.scene.value,'location':plan.location.value,'activity':plan.activity.value,'pose':plan.pose.value,'support_surface':plan.support_surface.value,'wardrobe':plan.wardrobe.value,'body_visibility':visibility,'expression_modifiers':exprs,'composition':plan.composition,'camera_mode':vr.camera_mode or plan.camera.value,'lighting':plan.lighting.value}
    common=['collage','watermark','text','logo','plastic skin','wax skin','studio catalogue pose','passport photo','ID photo','biometric headshot','centered casting headshot','impossible camera angle','impossible selfie arm','broken hands','malformed limbs','bad anatomy']
    if expected_subject_count == 0:
        neg_terms=['human person','visible face','visible body','model','portrait','photographer','camera operator','human reflection','background person','stranger','disembodied human body'] + common
    elif expected_subject_count == 2:
        neg_terms=['third person','background person','crowd','group photo','duplicated subject','twins','extra face','extra head','unrelated person','photobomb','reflected extra person','child','teenager','youthful appearance','non-consensual interaction','visible photographer'] + common
    else:
        neg_terms=['duplicate person','two people','second person','companion','photographer','camera operator','person in background','background people','extra face','extra head','extra body','reflected distinct person','mirror duplicate','duplicated subject','group photo','couple photo','selfie with another person','photobomb','disembodied hand from another person','cloned face'] + common
    neg_terms += list(plan.excluded_objects.value or []) + [x for x in plan.current_intent.get('explicit_exclusions', [])]
    if allowed_adult_intent:
        neg_terms.extend(['contradictory anatomy','mixed sex characteristics inconsistent with profile','malformed anatomy','ambiguous anatomy','duplicated body parts','anatomically inconsistent body','identity inconsistency'])
    if vr.framing_requirement == 'full_body':
        neg_terms.extend(['close-up','headshot','face-only portrait','shoulders-only crop','body cropped out of frame','missing legs','missing feet','tight portrait','body truncation'])
    if vr.face_hidden_required:
        neg_terms.extend(['visible face','recognizable face','reflected face','accidental headshot'])
    return CompiledImagePrompt(positive, ', '.join(dict.fromkeys(neg_terms)), {'width':plan.composition['width'],'height':plan.composition['height'],'seed':plan.seed_strategy.get('final_provider_seed')}, sec)

'''
regex_once('app/services/image_pipeline_v2.py', r'def compile_image_prompt\(.*?(?=def validate_compiled_prompt)', new_compile)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    expected_subject_count=int((plan.composition or {}).get('expected_subject_count') or 1)\n    if expected_subject_count == 2:\n        if 'exactly 2 fictional adults' not in positive or 'third person' not in compiled.negative_prompt: errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))\n    elif 'exactly one fictional adult' not in positive or 'two people' not in compiled.negative_prompt:\n        errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))\n''',
    '''    expected_raw=(plan.composition or {}).get('expected_subject_count')\n    expected_subject_count=int(expected_raw if expected_raw is not None else 1)\n    if expected_subject_count == 0:\n        if 'zero visible human people' not in positive or 'human person' not in compiled.negative_prompt: errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))\n    elif expected_subject_count == 2:\n        if 'exactly 2 fictional consenting adults' not in positive or 'third person' not in compiled.negative_prompt: errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))\n    elif 'exactly one fictional adult' not in positive or 'two people' not in compiled.negative_prompt:\n        errors.append(str(InvariantCode.SINGLE_SUBJECT_CONSTRAINT_MISSING))\n''',
)
replace_once(
    'app/services/image_pipeline_v2.py',
    '''    if str(expected_subject_count) not in positive and ('one' not in positive if expected_subject_count == 1 else 'two' not in positive):\n        errors.append(str(InvariantCode.SUBJECT_COUNT_MISMATCH))\n''',
    '''    if expected_subject_count == 0 and 'zero visible human people' not in positive:\n        errors.append(str(InvariantCode.SUBJECT_COUNT_MISMATCH))\n    elif expected_subject_count == 1 and 'exactly one fictional adult' not in positive:\n        errors.append(str(InvariantCode.SUBJECT_COUNT_MISMATCH))\n    elif expected_subject_count == 2 and 'exactly 2 fictional consenting adults' not in positive:\n        errors.append(str(InvariantCode.SUBJECT_COUNT_MISMATCH))\n''',
)

# ---------------------------------------------------------------------------
# Service adapter: store contract, attach world memory and pass it to retries.
# ---------------------------------------------------------------------------
replace_once(
    'app/services/image_generation_service.py',
    'from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_adult_scene_policy, select_generation_model\n',
    'from app.services.image_generation_guardrails import apply_semantic_safety_contract, apply_adult_scene_policy, select_generation_model\nfrom app.services.partner_photo_contract import attach_world_memory_context, build_partner_photo_contract\n',
)
replace_once(
    'app/services/image_generation_service.py',
    '''def _variation_requested(text: str, meta: dict | None = None) -> bool:\n    t=text or ''; m=meta or {}\n    return bool(m.get('contextual_followup') or m.get('route_type') in {'image_followup','image_refinement'} or re.search(r'یکی دیگه|یه دونه دیگه|variation|واریاسیون|مثل قبلی|این بار', t))\n''',
    '''def _variation_requested(text: str, meta: dict | None = None) -> bool:\n    m=meta or {}\n    return bool(m.get('contextual_followup') or m.get('route_type') in {'image_followup','image_refinement'} or m.get('route_action') in {'variation','refinement','refine_previous'})\n''',
)
replace_once(
    'app/services/image_generation_service.py',
    '''    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)\n''',
    '''    time_context, routine_slot, current_location, recent_conversation, relevant_memories, relationship_state, snapshot = _build_request_context(db, user, user_request)\n    intent.photo_contract=attach_world_memory_context(getattr(intent, 'photo_contract', {}), relevant_memories)\n''',
)
new_adapter = r'''def apply_semantic_visual_intent_to_v2_intent(intent, semantic_decision, *, resolved_visual_intent=None):
    """Copy the complete semantic partner-photo contract into V2 without losing detail."""
    if resolved_visual_intent is not None and isinstance(resolved_visual_intent, dict):
        from app.services.semantic_image_intent_router import VisualIntent
        resolved_visual_intent=VisualIntent(**resolved_visual_intent)
    if not resolved_visual_intent and (not semantic_decision or not getattr(semantic_decision, 'visual_intent', None)):
        return intent
    from app.services import image_pipeline_v2 as v2
    vi=resolved_visual_intent or semantic_decision.visual_intent
    free=list(getattr(vi, 'freeform_visual_constraints', []) or [])
    contract=build_partner_photo_contract(vi)
    intent.photo_contract=contract
    intent.expected_subject_count=int(contract.get('expected_human_subject_count', 1))

    if getattr(vi, 'scene', None): intent.scene.scene_key=vi.scene; intent.scene.explicit_current_request=True
    if getattr(vi, 'location', None): intent.scene.location=vi.location; intent.scene.explicit_current_request=True
    if getattr(vi, 'environment_type', None): intent.scene.environment_type=vi.environment_type
    if getattr(vi, 'privacy', None): intent.scene.privacy=vi.privacy
    if getattr(vi, 'required_visible_environment_elements', None): intent.scene.required_visible_environment_elements=list(vi.required_visible_environment_elements or [])
    if intent.scene.explicit_current_request:
        semantic_free=set(str(x) for x in free)
        intent.passthrough_visual_details=[x for x in intent.passthrough_visual_details if x in semantic_free]
        intent.parse_coverage.passthrough_visual_spans=[x for x in intent.parse_coverage.passthrough_visual_spans if x in semantic_free]
        logger.info('IMAGE_SEMANTIC_SCENE_RESOLVED user_id=%s job_id=%s request_chain_id=%s action=%s semantic_requested_scene=%s semantic_requested_location=%s', None, None, None, getattr(semantic_decision, 'action', None), intent.scene.scene_key, intent.scene.location)
    if getattr(vi, 'pose', None): intent.pose.pose=vi.pose
    if contract.get('back_to_camera') and not intent.pose.pose: intent.pose.pose='back_to_camera'
    if getattr(vi, 'activity', None): intent.visual_assertions.append(v2.VisualAssertion('subject','activity',vi.activity,(0,0),1.0))
    if getattr(vi, 'expression', None): intent.expression_modifiers.append(v2.ExpressionModifier('face','expression',vi.expression,(0,0)))
    if getattr(vi, 'wardrobe', None): intent.wardrobe.wardrobe=vi.wardrobe; intent.wardrobe.explicit_current_request=True
    intent.composition.camera=contract.get('camera_mode') or getattr(vi, 'camera', None)
    if getattr(vi, 'gaze_direction', None): intent.gaze_direction=vi.gaze_direction
    if getattr(vi, 'eye_contact_required', False) and not contract.get('face_hidden'): intent.eye_contact_required=True
    framing=contract.get('framing') or getattr(vi, 'framing', None)
    if framing:
        intent.composition.framing=framing
        if framing == 'full_body':
            intent.body_visibility.regions.setdefault('full_body', v2.BodyRegionIntent(mentioned=True, visibility_requested=True, framing_requested=True, explicit_current_request=True))
        logger.info('IMAGE_SEMANTIC_FRAMING_ADAPTED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s', None, None, None, getattr(semantic_decision, 'action', None), framing, 'semantic_visual_intent')

    for region in contract.get('required_body_regions') or []:
        current=intent.body_visibility.regions.setdefault(region, v2.BodyRegionIntent())
        current.mentioned=True; current.visibility_requested=True; current.framing_requested=True; current.explicit_current_request=True
    for region in contract.get('forbidden_body_regions') or []:
        current=intent.body_visibility.regions.setdefault(region, v2.BodyRegionIntent())
        current.mentioned=True; current.visibility_negated=True; current.explicit_current_request=True
    if contract.get('face_hidden'):
        if 'visible face' not in intent.explicit_exclusions: intent.explicit_exclusions.append('visible face')
    if contract.get('partner_visible') is False:
        intent.explicit_exclusions.extend(x for x in ['human person','visible partner','human face'] if x not in intent.explicit_exclusions)

    for obj in list(dict.fromkeys((getattr(vi, 'visible_objects', []) or []) + (contract.get('visible_objects') or []))):
        if obj: intent.scene.spatial_relations.append(v2.SpatialRelation('visible_object', obj)); free.append(obj)
    for obj in list(dict.fromkeys((getattr(vi, 'held_objects', []) or []) + (contract.get('held_objects') or []))):
        if obj: intent.scene.spatial_relations.append(v2.SpatialRelation('held_object', obj)); free.append(obj)
    if contract.get('primary_subject') == 'pet': free.append('established pet is the primary subject')
    elif contract.get('primary_subject') == 'object': free.append('requested object is the primary subject')
    elif contract.get('primary_subject') == 'scene': free.append('requested scene is the primary subject')

    intent=apply_semantic_safety_contract(intent, vi, getattr(semantic_decision, 'safety_relevant_signals', None) if semantic_decision is not None else None)
    for ex in getattr(vi, 'exclusions', []) or []:
        if ex: intent.explicit_exclusions.append(ex)
    if getattr(vi, 'expected_subject_count', None) is not None and contract.get('partner_visible', True): intent.expected_subject_count=vi.expected_subject_count
    for val, label in ((getattr(vi,'lighting',None),'lighting'), (getattr(vi,'subject_focus',None),'subject_focus')):
        if val: free.append(f'{label}: {val}')
    if free:
        cleaned=[v2.sanitize_passthrough_visual_detail(x) for x in dict.fromkeys(free) if v2.sanitize_passthrough_visual_detail(x)]
        intent.passthrough_visual_details=list(dict.fromkeys(intent.passthrough_visual_details + cleaned))
        intent.parse_coverage.passthrough_visual_spans=list(dict.fromkeys(intent.parse_coverage.passthrough_visual_spans + cleaned))
        intent.visual_assertions.extend(v2.VisualAssertion('freeform_visual_constraints','constraint',x,(0,0),1.0) for x in cleaned)
        if intent.parse_coverage.disposition == v2.ParseDisposition.COMPLETE:
            intent.parse_coverage.disposition=v2.ParseDisposition.BEST_EFFORT
    return intent

'''
regex_once('app/services/image_generation_service.py', r'def apply_semantic_visual_intent_to_v2_intent\(.*?(?=def image_generation_quote)', new_adapter)
replace_once(
    'app/services/image_generation_service.py',
    '''corrective_prompt_for_reasons(rejected_quality[-1]['reason_codes'], expected_subject_count=int((job.metadata_json or {}).get('expected_subject_count', 1)), expected_interaction=(job.metadata_json or {}).get('interaction'), secondary_subject_role=(job.metadata_json or {}).get('secondary_subject_role'), identity_requirements=(job.metadata_json or {}).get('identity_descriptor'))''',
    '''corrective_prompt_for_reasons(rejected_quality[-1]['reason_codes'], expected_subject_count=int((job.metadata_json or {}).get('expected_subject_count', 1)), expected_interaction=(job.metadata_json or {}).get('interaction'), secondary_subject_role=(job.metadata_json or {}).get('secondary_subject_role'), identity_requirements=(job.metadata_json or {}).get('identity_descriptor'), photo_contract=((job.metadata_json or {}).get('visual_requirements') or {}).get('photo_contract'))''',
)
replace_once(
    'app/services/image_generation_service.py',
    ''''visual_requirements':v2.asdict(plan.visual_requirements),'explicit_nudity_requested':''',
    ''''visual_requirements':v2.asdict(plan.visual_requirements),'photo_contract':dict(getattr(plan.visual_requirements,'photo_contract',{}) or {}),'explicit_nudity_requested':''',
)

# ---------------------------------------------------------------------------
# QA: validate requested primary subject, camera logic, face visibility and
# natural partner-photo realism in addition to existing anatomy/scene checks.
# ---------------------------------------------------------------------------
replace_once(
    'app/services/generated_image_qa_service.py',
    ''''anatomy_qa_consensus_incomplete','anatomy_qa_disagreement'\n''',
    ''''anatomy_qa_consensus_incomplete','anatomy_qa_disagreement','primary_subject_mismatch','requested_pet_missing','required_object_missing','unexpected_visible_partner','face_should_be_hidden','face_should_be_visible','back_view_mismatch','camera_mode_mismatch','implausible_camera_capture','id_photo_regression','hands_only_mismatch'\n''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''    ambiguous_anatomy: bool | None = None\n''',
    '''    ambiguous_anatomy: bool | None = None\n    primary_subject_matches_request: bool | None = None\n    pet_visible: bool | None = None\n    required_objects_visible: bool | None = None\n    partner_visible: bool | None = None\n    face_visible: bool | None = None\n    face_hidden_matches_request: bool | None = None\n    back_to_camera_matches_request: bool | None = None\n    camera_mode_matches_request: bool | None = None\n    natural_capture_plausible: bool | None = None\n    looks_like_id_photo: bool | None = None\n    hands_only_matches_request: bool | None = None\n''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''        if hasattr(self, 'consensus_passed'):\n            data['consensus_passed']=getattr(self, 'consensus_passed')\n        return data\n''',
    '''        if hasattr(self, 'consensus_passed'):\n            data['consensus_passed']=getattr(self, 'consensus_passed')\n        for name in ('primary_subject_matches_request','pet_visible','required_objects_visible','partner_visible','face_visible','face_hidden_matches_request','back_to_camera_matches_request','camera_mode_matches_request','natural_capture_plausible','looks_like_id_photo','hands_only_matches_request'):\n            if hasattr(self, name): data[name]=getattr(self, name)\n        return data\n''',
)
qa_prompt_replacement = 'QA_PROMPT=\'\'\'You are a fail-closed visual fulfillment and realism QA module for photos shared by a persistent fictional adult partner. Return JSON only. Do not identify any real person. Count visible non-reflected humans separately from a same-subject mirror reflection. Check the requested primary subject, required objects or pet, partner visibility, face shown or hidden, back-facing pose, framing, scene, camera method, and whether the capture is physically plausible. Reject passport, ID, casting-headshot defaults when a natural personal photo was requested. For object-only or pet-only requests, zero humans is correct and any visible human is a failure. For hands-only requests, verify only the requested hands or forearms are shown and no face, head, or torso appears. A physically consistent reflection of the same intended partner is not a second person. Schema: {"person_count":1,"face_count":1,"intended_subject_count":1,"unexpected_additional_person_visible":false,"background_extra_person_visible":false,"duplicate_subject_visible":false,"reflection_visible":false,"reflection_matches_primary_subject":true,"reflected_distinct_person_visible":false,"selfie_detected":false,"mirror_selfie_detected":false,"interaction_detected":null,"interaction_matches_request":true,"confidence":"high","framing":"medium","framing_matches_request":true,"full_body_visible":false,"head_inside_frame":true,"feet_inside_frame":true,"body_not_cropped":true,"requested_scene_visible":true,"requested_support_surface_visible":true,"requested_pose_matches":true,"identity_consistency_reasonable":true,"primary_subject_matches_request":true,"pet_visible":false,"required_objects_visible":true,"partner_visible":true,"face_visible":true,"face_hidden_matches_request":true,"back_to_camera_matches_request":true,"camera_mode_detected":"casual_phone_photo","camera_mode_matches_request":true,"natural_capture_plausible":true,"looks_like_id_photo":false,"hands_only_matches_request":true,"reason_codes":[]}\'\'\''
qa_prompt_replacement = 'QA_PROMPT=\'\'\'You are a fail-closed visual fulfillment and realism QA module for photos shared by a persistent fictional adult partner. Return JSON only. Do not identify any real person. Count visible non-reflected humans separately from a same-subject mirror reflection. Check the requested primary subject, required objects or pet, partner visibility, face shown or hidden, back-facing pose, framing, scene, camera method, and whether the capture is physically plausible. Reject passport, ID, casting-headshot defaults when a natural personal photo was requested. For object-only or pet-only requests, zero humans is correct and any visible human is a failure. For hands-only requests, verify only the requested hands or forearms are shown and no face, head, or torso appears. A physically consistent reflection of the same intended partner is not a second person. Schema: {"person_count":1,"face_count":1,"intended_subject_count":1,"unexpected_additional_person_visible":false,"background_extra_person_visible":false,"duplicate_subject_visible":false,"reflection_visible":false,"reflection_matches_primary_subject":true,"reflected_distinct_person_visible":false,"selfie_detected":false,"mirror_selfie_detected":false,"interaction_detected":null,"interaction_matches_request":true,"confidence":"high","framing":"medium","framing_matches_request":true,"full_body_visible":false,"head_inside_frame":true,"feet_inside_frame":true,"body_not_cropped":true,"requested_scene_visible":true,"requested_support_surface_visible":true,"requested_pose_matches":true,"identity_consistency_reasonable":true,"primary_subject_matches_request":true,"pet_visible":false,"required_objects_visible":true,"partner_visible":true,"face_visible":true,"face_hidden_matches_request":true,"back_to_camera_matches_request":true,"camera_mode_detected":"casual_phone_photo","camera_mode_matches_request":true,"natural_capture_plausible":true,"looks_like_id_photo":false,"hands_only_matches_request":true,"reason_codes":[]}\'\'\''
regex_once(
    'app/services/generated_image_qa_service.py',
    r"QA_PROMPT='''.*?'''",
    qa_prompt_replacement,
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''        'clothing_visibility_required': bool(vr.get('wardrobe_visibility_required')),\n    }\n''',
    '''        'clothing_visibility_required': bool(vr.get('wardrobe_visibility_required')),\n        'photo_contract': vr.get('photo_contract') or {},\n        'primary_subject': vr.get('primary_subject'),\n        'partner_visible': vr.get('partner_visible'),\n        'pet_visible': vr.get('pet_visible'),\n        'hands_only': vr.get('hands_only'),\n        'face_visible_required': vr.get('face_visible_required'),\n        'face_hidden_required': vr.get('face_hidden_required'),\n        'back_to_camera_required': vr.get('back_to_camera_required'),\n        'camera_mode': vr.get('camera_mode'),\n        'natural_capture_required': vr.get('natural_capture_required'),\n        'required_visible_objects': vr.get('required_objects') or must.get('required_visible_objects') or [],\n    }\n''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''    if person_count == 0: codes.extend(['missing_primary_subject','missing_subject'])\n''',
    '''    if person_count == 0 and expected_subject_count > 0: codes.extend(['missing_primary_subject','missing_subject'])\n    if expected_subject_count == 0 and person_count is not None and person_count > 0: codes.append('unexpected_visible_partner')\n''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''    if identity_ok is False: codes.append('identity_inconsistent')\n''',
    '''    contract=vr.get('photo_contract') or {}\n    primary_subject_matches=None if payload.get('primary_subject_matches_request') is None else _bool(payload.get('primary_subject_matches_request'))\n    pet_visible=None if payload.get('pet_visible') is None else _bool(payload.get('pet_visible'))\n    required_objects_visible=None if payload.get('required_objects_visible') is None else _bool(payload.get('required_objects_visible'))\n    partner_visible_detected=None if payload.get('partner_visible') is None else _bool(payload.get('partner_visible'))\n    face_visible_detected=None if payload.get('face_visible') is None else _bool(payload.get('face_visible'))\n    face_hidden_matches=None if payload.get('face_hidden_matches_request') is None else _bool(payload.get('face_hidden_matches_request'))\n    back_matches=None if payload.get('back_to_camera_matches_request') is None else _bool(payload.get('back_to_camera_matches_request'))\n    camera_matches=None if payload.get('camera_mode_matches_request') is None else _bool(payload.get('camera_mode_matches_request'))\n    natural_capture=None if payload.get('natural_capture_plausible') is None else _bool(payload.get('natural_capture_plausible'))\n    looks_like_id=_bool(payload.get('looks_like_id_photo'))\n    hands_only_matches=None if payload.get('hands_only_matches_request') is None else _bool(payload.get('hands_only_matches_request'))\n    if contract.get('primary_subject') in {'pet','object','scene'} and primary_subject_matches is not True: codes.append('primary_subject_mismatch')\n    if contract.get('pet_visible') and pet_visible is not True: codes.append('requested_pet_missing')\n    if (vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects')) and required_objects_visible is not True: codes.append('required_object_missing')\n    if contract.get('partner_visible') is False and partner_visible_detected is not False: codes.append('unexpected_visible_partner')\n    if contract.get('face_hidden') and face_hidden_matches is not True: codes.append('face_should_be_hidden')\n    if contract.get('face_visible') is True and face_visible_detected is not True: codes.append('face_should_be_visible')\n    if contract.get('back_to_camera') and back_matches is not True: codes.append('back_view_mismatch')\n    if contract.get('camera_mode') and camera_matches is not True: codes.append('camera_mode_mismatch')\n    if contract.get('natural_capture_required', True) and (natural_capture is not True or looks_like_id): codes.append('id_photo_regression' if looks_like_id else 'implausible_camera_capture')\n    if contract.get('hands_only') and hands_only_matches is not True: codes.append('hands_only_mismatch')\n    if identity_ok is False and contract.get('identity_visibility_scope') != 'absent': codes.append('identity_inconsistent')\n''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''    if requested_full_body and not result.passed: logger.info('IMAGE_FULL_BODY_QA_FAILED''',
    '''    result.primary_subject_matches_request=primary_subject_matches\n    result.pet_visible=pet_visible\n    result.required_objects_visible=required_objects_visible\n    result.partner_visible=partner_visible_detected\n    result.face_visible=face_visible_detected\n    result.face_hidden_matches_request=face_hidden_matches\n    result.back_to_camera_matches_request=back_matches\n    result.camera_mode_matches_request=camera_matches\n    result.natural_capture_plausible=natural_capture\n    result.looks_like_id_photo=looks_like_id\n    result.hands_only_matches_request=hands_only_matches\n    if requested_full_body and not result.passed: logger.info('IMAGE_FULL_BODY_QA_FAILED''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''    if bool(vr.get('full_body_visible') or vr.get('framing_requirement') == 'full_body' or metadata.get('full_body_required')):\n        return bool(metadata.get('qa_requested_framing') == 'full_body' and qa.get('framing_matches_request') is True and qa.get('requested_full_body_visible') is True and qa.get('head_inside_frame') is True and qa.get('feet_inside_frame') is True and qa.get('body_not_cropped') is True and 'framing_mismatch' not in (qa.get('reason_codes') or []) and 'closeup_forbidden' not in (qa.get('reason_codes') or []))\n    return True\n''',
    '''    contract=vr.get('photo_contract') or {}\n    if contract.get('primary_subject') in {'pet','object','scene'} and qa.get('primary_subject_matches_request') is not True: return False\n    if contract.get('pet_visible') and qa.get('pet_visible') is not True: return False\n    if (vr.get('required_objects') or (vr.get('must_satisfy') or {}).get('required_visible_objects')) and qa.get('required_objects_visible') is not True: return False\n    if contract.get('partner_visible') is False and qa.get('partner_visible') is not False: return False\n    if contract.get('face_hidden') and qa.get('face_hidden_matches_request') is not True: return False\n    if contract.get('face_visible') is True and qa.get('face_visible') is not True: return False\n    if contract.get('back_to_camera') and qa.get('back_to_camera_matches_request') is not True: return False\n    if contract.get('camera_mode') and qa.get('camera_mode_matches_request') is not True: return False\n    if contract.get('natural_capture_required', True) and (qa.get('natural_capture_plausible') is not True or qa.get('looks_like_id_photo') is True): return False\n    if contract.get('hands_only') and qa.get('hands_only_matches_request') is not True: return False\n    if bool(vr.get('full_body_visible') or vr.get('framing_requirement') == 'full_body' or metadata.get('full_body_required')):\n        return bool(metadata.get('qa_requested_framing') == 'full_body' and qa.get('framing_matches_request') is True and qa.get('requested_full_body_visible') is True and qa.get('feet_inside_frame') is True and qa.get('body_not_cropped') is True and 'framing_mismatch' not in (qa.get('reason_codes') or []) and 'closeup_forbidden' not in (qa.get('reason_codes') or []))\n    return True\n''',
)
replace_once(
    'app/services/generated_image_qa_service.py',
    '''    elif 'identity_inconsistent' in codes:\n        msg='چهره به‌اندازه کافی ثابت درنیومد؛ سکه‌ات برگشت.'\n''',
    '''    elif codes & {'primary_subject_mismatch','requested_pet_missing','required_object_missing','unexpected_visible_partner','face_should_be_hidden','face_should_be_visible','back_view_mismatch','camera_mode_mismatch','implausible_camera_capture','id_photo_regression','hands_only_mismatch'}:\n        msg='این یکی شبیه عکسی که گفتی درنیومد؛ نفرستادمش و سکه‌ات برگشت 🤍'\n    elif 'identity_inconsistent' in codes:\n        msg='این بار شبیه همون آدم همیشگی درنیومد؛ نفرستادمش و سکه‌ات برگشت 🤍'\n''',
)
new_corrective = r'''def corrective_prompt_for_reasons(reason_codes: list[str], *, expected_subject_count:int=1, expected_interaction:str|None=None, secondary_subject_role:str|None=None, identity_requirements:dict|None=None, photo_contract:dict|None=None) -> str:
    codes=set(reason_codes or [])
    if not codes & REASON_CODES: return ''
    contract=photo_contract or {}
    lines=['\nSTRICT PARTNER-PHOTO CORRECTION:']
    if expected_subject_count == 0:
        lines.append('Render zero visible human people. The requested pet/object/scene is the primary subject. No face, body, stranger, photographer, or human reflection.')
    elif expected_subject_count == 2:
        interaction_line='They are mutually and consensually kissing.' if expected_interaction == 'kiss' else 'They have the requested consensual romantic interaction.'
        lines.extend([f"Render exactly two fictional adults: the stored partner and one adult {secondary_subject_role or 'companion'}.", interaction_line, 'No third person, background people, duplicates, or reflections of additional people.'])
    else:
        lines.extend(['Render exactly one fictional adult matching the stored partner identity.', 'No companion, photographer, second person, background people, duplicate face/body, or reflected distinct person.'])
    if codes & {'framing_mismatch','missing_full_body','missing_feet','cropped_body','missing_head','closeup_forbidden'}:
        lines.append('Correct the framing exactly: full body head-to-feet when requested, camera farther away, no close-up and no crop.')
    if codes & {'primary_subject_mismatch','requested_pet_missing','required_object_missing'}:
        lines.append('Make the requested pet/object/scene unmistakably the primary visible subject and include every required object.')
    if codes & {'face_should_be_hidden','hands_only_mismatch'}:
        lines.append('Keep the face and head completely outside the frame. For hands-only, show only natural hands/forearms interacting with the requested object.')
    if 'face_should_be_visible' in codes: lines.append('Show the same recognizable stored face naturally and clearly.')
    if 'back_view_mismatch' in codes: lines.append('Keep the partner naturally turned away from the camera; do not rotate to a front-facing portrait.')
    if codes & {'camera_mode_mismatch','implausible_camera_capture','id_photo_regression'}:
        lines.append('Use the requested physically plausible phone/mirror/tripod/POV camera method. Make it a spontaneous personal photo, never a passport, ID, casting, or studio headshot.')
    if codes & {'identity_inconsistent'}:
        lines.append('Preserve the exact stored face family, gender presentation, age appearance, hair, skin tone, body build and distinguishing features.')
    if codes & {'anatomy_profile_inconsistent','contradictory_sex_characteristics','malformed_anatomy','implausible_anatomy','duplicated_anatomy_parts','missing_expected_parts_when_visible','ambiguous_anatomy','anatomy_not_assessable'}:
        lines.append('Preserve the stored adult identity and anatomical profile with coherent realistic body proportions; no malformed, duplicated, contradictory, or ambiguous structure.')
    lines.extend(prompt_constraints(contract))
    return '\n'.join(lines)
'''
regex_once('app/services/generated_image_qa_service.py', r'def corrective_prompt_for_reasons\(.*\Z', new_corrective)

# ---------------------------------------------------------------------------
# Telegram UX: natural partner language instead of queue/system copy.
# ---------------------------------------------------------------------------
replace_once(
    'app/api/telegram.py',
    '''            "باشه، الان یه عکس برات می‌فرستم.",\n''',
    '''            __import__('app.services.partner_photo_contract', fromlist=['image_acknowledgement']).image_acknowledgement(getattr(job, 'metadata_json', None)),\n''',
)
regex_once(
    'app/api/telegram.py',
    r'''def _image_status_text\(job_summary\):.*?    return None\n''',
    '''def _image_status_text(job_summary):\n    if not job_summary: return None\n    from app.services.partner_photo_contract import image_status_text\n    return image_status_text(getattr(job_summary, 'status', None), getattr(job_summary, 'error_code', None))\n''',
)
replace_once(
    'app/api/telegram.py',
    '''          clarification = "منظورت عکس جدیده، تغییر عکس قبلیه، یا فقط داری درباره‌ش حرف می‌زنی؟"\n''',
    '''          clarification = "این رو یه عکس تازه بگیرم یا همون عکس قبلی رو تغییر بدم؟"\n''',
)
replace_once(
    'app/api/telegram.py',
    '''            await _send_user_text(telegram_service, chat_id, "این نوع عکس رو نمی‌تونم بفرستم، ولی می‌تونم یه عکس عادی یا عاشقانه‌ی امن بفرستم.", user_id=user.id, surface="chat", user_text=user_text)\n''',
    '''            await _send_user_text(telegram_service, chat_id, "این بار نتونستم عکس رو درست آماده کنم؛ همون چیزی که می‌خوای رو دوباره بگو تا از نو بگیرمش.", user_id=user.id, surface="chat", user_text=user_text)\n''',
)

# ---------------------------------------------------------------------------
# Focused regression tests.
# ---------------------------------------------------------------------------
Path('tests/test_partner_photo_engine.py').write_text(r'''from types import SimpleNamespace

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


def test_detailed_photo_request_is_not_collapsed_to_empty_deterministic_action():
    assert canonical_explicit_image_action('یه عکس از قهوه ات بده فقط دستات معلوم باشه') is None
    assert canonical_explicit_image_action('عکس بده پشت به دوربین باشی') is None


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
''', encoding='utf-8')

print('partner photo engine patch applied')
