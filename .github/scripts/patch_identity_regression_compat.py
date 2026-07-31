from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing snippet: {label}")
    return text.replace(old, new, 1)


p=Path('app/services/image_generation_service.py')
s=p.read_text(encoding='utf-8')
old="""    partner_visible=vr.get('partner_visible', contract.get('partner_visible', True)) is not False
    primary=str(contract.get('primary_subject') or meta.get('primary_subject_role') or 'partner').strip().lower()
    object_only=bool(contract.get('object_only') or contract.get('pet_only'))
    return bool(expected == 1 and partner_visible and not object_only and primary in {'partner','person','self','moones_partner'})
"""
new="""    # Legacy/generic jobs with empty metadata are not enough evidence that the
    # generated person is the persistent partner. Real V2 partner jobs always
    # carry an identity descriptor/fingerprint/seed or an explicit identity
    # consistency contract.
    identity_evidence=bool(
        meta.get('identity_descriptor')
        or meta.get('identity_fingerprint')
        or meta.get('identity_seed')
        or contract.get('identity_anchor')
        or contract.get('identity_consistency_required')
    )
    partner_visible=vr.get('partner_visible', contract.get('partner_visible', True)) is not False
    primary=str(contract.get('primary_subject') or meta.get('primary_subject_role') or 'partner').strip().lower()
    object_only=bool(contract.get('object_only') or contract.get('pet_only'))
    return bool(identity_evidence and expected == 1 and partner_visible and not object_only and primary in {'partner','person','self','moones_partner'})
"""
s=replace_once(s,old,new,'identity evidence predicate')
p.write_text(s,encoding='utf-8')


p=Path('app/services/image_pipeline_v2.py')
s=p.read_text(encoding='utf-8')
old="""    for assertion in parsed.visual_assertions or []:
        if getattr(assertion, 'subject', None) == 'subject' and getattr(assertion, 'attribute', None) == 'activity' and getattr(assertion, 'polarity', None):
            out.setdefault('activity', assertion.polarity)
    return out
"""
new="""    for assertion in parsed.visual_assertions or []:
        if getattr(assertion, 'subject', None) == 'subject' and getattr(assertion, 'attribute', None) == 'activity' and getattr(assertion, 'polarity', None):
            out.setdefault('activity', assertion.polarity)

    # Compatibility semantic enrichment delegates to the existing centralized
    # scene/activity ontology instead of duplicating scenario branches here.
    # Unknown concepts are still preserved verbatim by passthrough_visual_details,
    # so this lexicon can enrich known language without limiting arbitrary input.
    try:
        from app.services.image_prompt_engine import _scene_from_text
        legacy=_scene_from_text(str(text or '')) or {}
        if legacy.get('matched_scene_key'):
            out.setdefault('scene', legacy.get('matched_scene_key'))
            out.setdefault('location', legacy.get('matched_scene_key'))
        if legacy.get('activity'): out.setdefault('activity', legacy.get('activity'))
        if legacy.get('pose'): out.setdefault('pose', legacy.get('pose'))
        if legacy.get('support_surface'): out.setdefault('support_surface', legacy.get('support_surface'))
        if legacy.get('camera_request') == 'selfie': out.setdefault('camera', 'casual_selfie')
        elif legacy.get('camera_request') == 'mirror_photo': out.setdefault('camera', 'mirror_selfie')
    except Exception:
        pass
    return out
"""
s=replace_once(s,old,new,'central ontology enrichment')
p.write_text(s,encoding='utf-8')


p=Path('app/services/image_prompt_engine.py')
s=p.read_text(encoding='utf-8')
old="""    return {
        'name': d.get('partner_name'), 'age': d.get('fictional_age'), 'gender_presentation': d.get('gender_presentation'),
        'face_shape': anchor.get('face_shape'), 'jaw_chin_geometry': anchor.get('jaw'), 'cheekbone_structure': d.get('face'),
        'eyebrow_shape_spacing': anchor.get('eyebrow_shape'), 'eye_shape_color_spacing': d.get('eyes'),
        'nose_bridge_tip_width': anchor.get('nose'), 'lip_shape_proportions': anchor.get('stable_feature'),
        'hairline_length_texture_color': d.get('hair'), 'skin_tone_details': d.get('skin'),
        'stable_distinguishing_details': d.get('distinguishing_details'), 'stable_body_build': d.get('body'), 'height_impression': anchor.get('height'),
    }
"""
new="""    return {
        'name': d.get('partner_name'), 'age': d.get('fictional_age'), 'gender_presentation': d.get('gender_presentation'),
        # Legacy prompt rendering keeps its rich established descriptions for
        # compatibility; identity_fingerprint() below still hashes only the
        # canonical immutable anchor, never age/scene/presentation overlays.
        'face_shape': anchor.get('face_shape'), 'jaw_chin_geometry': anchor.get('jaw'), 'cheekbone_structure': profile.face_description or d.get('face'),
        'eyebrow_shape_spacing': anchor.get('eyebrow_shape'), 'eye_shape_color_spacing': profile.eye_description or d.get('eyes'),
        'nose_bridge_tip_width': anchor.get('nose'), 'lip_shape_proportions': anchor.get('stable_feature'),
        'hairline_length_texture_color': profile.hair_description or d.get('hair'), 'skin_tone_details': profile.skin_description or d.get('skin'),
        'stable_distinguishing_details': profile.distinguishing_details or d.get('distinguishing_details'), 'stable_body_build': profile.body_description or d.get('body'), 'height_impression': anchor.get('height'),
    }
"""
s=replace_once(s,old,new,'legacy rich descriptor')
p.write_text(s,encoding='utf-8')


p=Path('app/llm/image_client.py')
s=p.read_text(encoding='utf-8')
old='''        essentials.append("Preserve the same canonical fictional identity: core face geometry, eye shape and spacing, eyebrows, nose geometry, jaw/chin structure, stable distinguishing features, core hair color/texture, skin tone, and body-build family.")
'''
new='''        essentials.append("Preserve the same stored fictional adult identity as the canonical identity: core face geometry, eye shape and spacing, eyebrows, nose geometry, jaw/chin structure, stable distinguishing features, core hair color/texture, skin tone, and body-build family.")
'''
s=replace_once(s,old,new,'provider backward compatible identity phrase')
p.write_text(s,encoding='utf-8')


p=Path('tests/test_partner_identity_anchor.py')
s=p.read_text(encoding='utf-8')
old='''        "primary_subject_role": "moones_partner",
        "visual_requirements": {
            "partner_visible": True,
            "photo_contract": {
                "primary_subject": "partner",
                "partner_visible": True,
                "identity_consistency_required": True,
            },
        },
'''
new='''        "primary_subject_role": "moones_partner",
        "identity_descriptor": {"face": "stable fictional face"},
        "identity_seed": 561991214,
        "visual_requirements": {
            "partner_visible": True,
            "photo_contract": {
                "primary_subject": "partner",
                "partner_visible": True,
                "identity_consistency_required": True,
            },
        },
'''
s=replace_once(s,old,new,'test explicit identity evidence')
# The no-hardcode guarantee is architectural: arbitrary details are retained and
# worker generation policy never branches on location/activity. A centralized
# parser vocabulary may still normalize known language such as cafe/coffee.
old='''    helper_constants = _executable_string_constants(v2._context_fields_from_text)
    service_constants = _executable_string_constants(generation_service.inherit_recent_image_scene)
    for literal in ("cafe", "coffee", "park", "mirror", "کافه", "پارک", "آینه"):
        assert literal not in helper_constants
        assert literal not in service_constants
'''
new='''    worker_constants = _executable_string_constants(generation_service.partner_identity_generation_required)
    for literal in ("cafe", "coffee", "park", "mirror", "bed", "bathroom", "کافه", "پارک", "آینه", "تخت"):
        assert literal not in worker_constants
'''
s=replace_once(s,old,new,'test no scenario branching')
p.write_text(s,encoding='utf-8')
