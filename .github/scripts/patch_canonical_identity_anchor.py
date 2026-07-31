from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing snippet: {label}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, repl: str, label: str, flags=0) -> str:
    out, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"regex replacement failed {label}: {count}")
    return out


p = Path("app/services/image_pipeline_v2.py")
s = p.read_text(encoding="utf-8")
s = replace_once(s, "PROFILE_SCHEMA_VERSION = 3", "PROFILE_SCHEMA_VERSION = 4", "profile schema version")
s = replace_once(
    s,
    "from app.services.partner_photo_contract import prompt_constraints\n",
    "from app.services.partner_photo_contract import prompt_constraints\nfrom app.services.partner_identity_anchor import (\n    derive_identity_anchor, identity_anchor_fingerprint, ensure_identity_anchor,\n    sync_mutable_profile_overlays, identity_descriptor_from_anchor,\n)\n",
    "identity helper import",
)

new_profile_block = '''def ensure_visual_profile_v2(db: Session, user: User, profile: PartnerVisualProfile) -> PartnerVisualProfile:
    # Identity and editable presentation are separate contracts. Ordinary partner
    # edits (age/name today; future mutable presentation fields later) must never
    # regenerate the canonical face/body anchor or the profile seed.
    traits=dict(profile.profile_json or {})
    ensure_identity_anchor(profile)
    overlays=sync_mutable_profile_overlays(user, profile)
    traits=dict(profile.profile_json or {})

    current_anatomy=normalize_anatomical_profile(getattr(profile, 'anatomical_profile', None) or traits.get('anatomical_profile'))
    anatomy_source=str(traits.get('anatomical_profile_source') or '')
    if current_anatomy == 'unspecified':
        current_anatomy=normalize_anatomical_profile(getattr(user, 'partner_gender', None))
        if current_anatomy != 'unspecified':
            anatomy_source='inferred_user_partner_gender'
    if current_anatomy == 'unspecified':
        current_anatomy=normalize_anatomical_profile(getattr(profile, 'gender_presentation', None))
        if current_anatomy != 'unspecified':
            anatomy_source='inferred_gender_presentation'
    profile.anatomical_profile=current_anatomy
    if current_anatomy != 'unspecified':
        traits['anatomical_profile']=current_anatomy
        traits['anatomical_profile_source']=anatomy_source or 'explicit_profile'

    # Never rotate an established identity seed because a mutable profile field
    # changed. Only legacy invalid seeds are deterministically normalized once.
    if profile.base_seed < VENICE_SEED_MIN:
        profile.base_seed = resolve_seed(abs(profile.base_seed or profile.user_id), profile.user_id, 'identity')['identity_seed']

    anchor, anchor_fp=ensure_identity_anchor(profile)
    traits=dict(profile.profile_json or {})
    traits['mutable_profile_overlays']=overlays
    traits['identity_anchor']=anchor
    traits['identity_anchor_fingerprint']=anchor_fp
    traits['schema_version']=PROFILE_SCHEMA_VERSION
    profile.profile_json=traits
    profile.version=PROFILE_SCHEMA_VERSION
    profile.updated_at=datetime.utcnow()
    db.flush()
    # Compatibility descriptor is refreshed after mutable overlays are applied;
    # the canonical fingerprint remains independent from those overlays.
    traits=dict(profile.profile_json or {})
    traits['identity_compatibility_descriptor']=identity_descriptor_v2(profile)
    profile.profile_json=traits
    db.flush()
    return profile


def identity_descriptor_v2(profile: PartnerVisualProfile) -> dict:
    return identity_descriptor_from_anchor(profile)

'''
s = sub_once(
    s,
    r"def ensure_visual_profile_v2\(db: Session, user: User, profile: PartnerVisualProfile\) -> PartnerVisualProfile:.*?(?=def _compatible_surfaces_for_pose)",
    new_profile_block,
    "profile/descriptor block",
    flags=re.S,
)

new_context_helper = '''def _context_fields_from_text(text: str) -> dict:
    """Recover only structured semantics that the generic parser can resolve.

    Unknown/free-form locations, activities and visual concepts stay in the
    passthrough contract. This function intentionally contains no product list of
    cafes, bedrooms, parks, mirrors, streets or any other scenario.
    """
    try:
        parsed=parse_image_intent(normalize_request_v2(str(text or '')))
    except Exception:
        return {}
    out={}
    if parsed.scene.scene_key: out['scene']=parsed.scene.scene_key
    if parsed.scene.location: out['location']=parsed.scene.location
    if parsed.scene.environment_type: out['environment_type']=parsed.scene.environment_type
    if parsed.scene.support_surface: out['support_surface']=parsed.scene.support_surface
    if parsed.pose.pose: out['pose']=parsed.pose.pose
    if parsed.wardrobe.wardrobe: out['wardrobe']=parsed.wardrobe.wardrobe
    if parsed.composition.camera: out['camera']=parsed.composition.camera
    if parsed.composition.framing: out['framing']=parsed.composition.framing
    for assertion in parsed.visual_assertions or []:
        if getattr(assertion, 'subject', None) == 'subject' and getattr(assertion, 'attribute', None) == 'activity' and getattr(assertion, 'polarity', None):
            out.setdefault('activity', assertion.polarity)
    return out

'''
s = sub_once(
    s,
    r"def _context_fields_from_text\(text: str\) -> dict:.*?(?=def merge_image_intent)",
    new_context_helper,
    "generic context helper",
    flags=re.S,
)
s = sub_once(
    s,
    r"\s*# lightweight passthrough-to-field extraction for current text/corrections\n\s*for k,v in _context_fields_from_text\(json\.dumps\(current_intent\.current_intent if hasattr\(current_intent,'current_intent'\) else current_intent\.parse_coverage\.passthrough_visual_spans, ensure_ascii=False\)\)\.items\(\):\n\s*setf\(k, v, Provenance\.EXPLICIT, True\)\n",
    "\n    # Free-form current-request details remain authoritative passthrough data;\n    # do not reinterpret them through scenario-specific fallback maps.\n",
    "remove hardcoded current fallback",
)

old_fp = """    ident=identity_descriptor_v2(profile)\n    fp_ident=dict(ident)\n    if visual_requirements.explicit_nudity_requested:\n        fp_ident['anatomical_profile']=visual_requirements.anatomical_profile\n    identity_fp=hashlib.sha256(json.dumps(fp_ident,sort_keys=True).encode()).hexdigest(); action=str(canonical_image_action(intent.continuity.action))\n"""
new_fp = """    ident=identity_descriptor_v2(profile)\n    identity_anchor, identity_fp=ensure_identity_anchor(profile)\n    profile_overlays=dict((profile.profile_json or {}).get('mutable_profile_overlays') or {})\n    action=str(canonical_image_action(intent.continuity.action))\n"""
s = replace_once(s, old_fp, new_fp, "canonical fingerprint")
old_continuity = "'anchor_features':{k:v for k,v in ident.items() if k in {'face','hair','eyes','skin','body','distinguishing_details','fictional_age'}},'continuity_summary':'Preserve the same stored partner identity; vary scene/composition only.'"
new_continuity = "'anchor_features':identity_anchor,'profile_overlays':profile_overlays,'continuity_summary':'Preserve the exact canonical partner identity. Apply mutable profile overlays independently; request/context may vary scene, activity, styling and composition but must never redesign the person.'"
s = replace_once(s, old_continuity, new_continuity, "continuity anchor")
old_identity_lock = """            sections.append('Identity lock: preserve the same recognizable person across requests; keep face shape, eye shape, eyebrow structure, hair style and hairline, skin tone, age appearance, body build and distinguishing details anchored to the stored fingerprint.')\n        sections.append('Never change the stored gender presentation or anatomical profile. Do not replace the partner with a generic woman or generic man.')\n"""
new_identity_lock = """            sections.append('Canonical identity lock: preserve the exact same recognizable fictional person across every request. Keep core face geometry, eye shape and spacing, eyebrow structure, nose geometry, jaw/chin structure, stable distinguishing features, core hair color/texture, skin tone and body-build family anchored to the stored canonical fingerprint.')\n            if desc.get('fictional_age') not in (None, ''):\n                sections.append(f\"Mutable profile overlay: render this same canonical person at fictional age {desc.get('fictional_age')}. An age edit changes age appearance only; it must never redesign or replace the canonical identity.\")\n            sections.append('Request/context variables such as environment, activity, wardrobe, temporary hair state, camera, lighting, pose and framing may change freely when requested, but they must never mutate the canonical identity.')\n        sections.append('Never change the stored gender presentation or anatomical profile. Do not replace the partner with a generic woman or generic man.')\n"""
s = replace_once(s, old_identity_lock, new_identity_lock, "prompt identity lock")
p.write_text(s, encoding="utf-8")


p = Path("app/services/image_generation_service.py")
s = p.read_text(encoding="utf-8")
old_override = '''def _explicit_context_overrides(text: str) -> tuple[str | None, str | None]:
    t = text or ''
    time_map = [('نیمه‌شب','late_night'),('نیمه شب','late_night'),('صبح','morning'),('ظهر','noon'),('عصر','evening'),('غروب','evening'),('شب','night')]
    loc_map = [('خانه','خانه'),('خونه','خانه'),('کافه','کافه'),('خیابان','خیابان')]
    return next((v for k,v in time_map if k in t), None), next((v for k,v in loc_map if k in t), None)
'''
new_override = '''def _explicit_context_overrides(text: str) -> tuple[str | None, str | None]:
    t = text or ''
    time_map = [('نیمه‌شب','late_night'),('نیمه شب','late_night'),('صبح','morning'),('ظهر','noon'),('عصر','evening'),('غروب','evening'),('شب','night')]
    explicit_time=next((v for k,v in time_map if k in t), None)
    explicit_location=None
    try:
        from app.services import image_pipeline_v2 as v2
        parsed=v2.parse_image_intent(v2.normalize_request_v2(t))
        explicit_location=parsed.scene.location or parsed.scene.scene_key
    except Exception:
        explicit_location=None
    return explicit_time, explicit_location
'''
s = replace_once(s, old_override, new_override, "generic explicit context override")
s = sub_once(
    s,
    r"\n        if not \(scene_key or location\):\n            normalized=.*?\n            match=next\(\(row for row in aliases if row\[0\] in normalized\), None\)\n            if match:\n                _, scene_key, location, environment_type, privacy=match\n",
    "\n",
    "remove recent scene alias list",
    flags=re.S,
)
p.write_text(s, encoding="utf-8")


p = Path("app/services/image_prompt_engine.py")
s = p.read_text(encoding="utf-8")
# Legacy/shadow helpers must use the same canonical fingerprint contract so an
# age edit cannot appear as an identity replacement in either execution path.
s = replace_once(
    s,
    "from app.services.addon_service import ADULT_IMAGE_GENERATION_UNLOCK, user_owns_addon, user_addon_enabled\n",
    "from app.services.addon_service import ADULT_IMAGE_GENERATION_UNLOCK, user_owns_addon, user_addon_enabled\nfrom app.services.partner_identity_anchor import derive_identity_anchor, identity_anchor_fingerprint, identity_descriptor_from_anchor\n",
    "legacy anchor import",
)
new_legacy_identity = '''def stable_identity_descriptor(profile: PartnerVisualProfile) -> dict:
    d=identity_descriptor_from_anchor(profile)
    anchor=derive_identity_anchor(profile)
    return {
        'name': d.get('partner_name'), 'age': d.get('fictional_age'), 'gender_presentation': d.get('gender_presentation'),
        'face_shape': anchor.get('face_shape'), 'jaw_chin_geometry': anchor.get('jaw'), 'cheekbone_structure': d.get('face'),
        'eyebrow_shape_spacing': anchor.get('eyebrow_shape'), 'eye_shape_color_spacing': d.get('eyes'),
        'nose_bridge_tip_width': anchor.get('nose'), 'lip_shape_proportions': anchor.get('stable_feature'),
        'hairline_length_texture_color': d.get('hair'), 'skin_tone_details': d.get('skin'),
        'stable_distinguishing_details': d.get('distinguishing_details'), 'stable_body_build': d.get('body'), 'height_impression': anchor.get('height'),
    }


def identity_fingerprint(profile: PartnerVisualProfile) -> str:
    return identity_anchor_fingerprint(profile)

'''
s = sub_once(
    s,
    r"def stable_identity_descriptor\(profile: PartnerVisualProfile\) -> dict:.*?(?=def identity_prompt_block)",
    new_legacy_identity,
    "legacy identity helpers",
    flags=re.S,
)
p.write_text(s, encoding="utf-8")
