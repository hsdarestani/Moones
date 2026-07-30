from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/services/image_pipeline_v2.py",
    """ANATOMICAL_PROFILE_VALUES={'male','female','intersex','unspecified'}
def normalize_anatomical_profile(value) -> str:
    v=str(value or '').strip().lower()
    aliases={'man':'male','male':'male','m':'male','female':'female','woman':'female','f':'female','intersex':'intersex','unspecified':'unspecified','unknown':'unspecified','prefer_not_to_say':'unspecified'}
    return aliases.get(v, 'unspecified')


def anatomical_profile_source(profile: PartnerVisualProfile) -> str:
    ap=normalize_anatomical_profile(getattr(profile, 'anatomical_profile', None) or (profile.profile_json or {}).get('anatomical_profile'))
    return 'explicit_profile' if ap != 'unspecified' else 'unspecified'
""",
    """ANATOMICAL_PROFILE_VALUES={'male','female','intersex','unspecified'}
def normalize_anatomical_profile(value) -> str:
    v=str(value or '').strip().lower().replace('‌', ' ')
    aliases={
        'man':'male','male':'male','m':'male','masculine':'male','boy':'male',
        'مرد':'male','پسر':'male','مذکر':'male','آقا':'male',
        'female':'female','woman':'female','f':'female','feminine':'female','girl':'female',
        'زن':'female','دختر':'female','مونث':'female','مؤنث':'female','خانم':'female',
        'intersex':'intersex','اینترسکس':'intersex','میان جنسی':'intersex',
        'unspecified':'unspecified','unknown':'unspecified','prefer_not_to_say':'unspecified',
        'neutral':'unspecified','nonbinary':'unspecified','non-binary':'unspecified','غیردودویی':'unspecified',
    }
    return aliases.get(v, 'unspecified')


def anatomical_profile_source(profile: PartnerVisualProfile) -> str:
    traits=profile.profile_json or {}
    ap=normalize_anatomical_profile(getattr(profile, 'anatomical_profile', None) or traits.get('anatomical_profile'))
    return str(traits.get('anatomical_profile_source') or ('explicit_profile' if ap != 'unspecified' else 'unspecified'))
""",
)

replace_once(
    "app/services/image_pipeline_v2.py",
    """    traits=dict(profile.profile_json or {})
    current_anatomy=normalize_anatomical_profile(getattr(profile, 'anatomical_profile', None) or traits.get('anatomical_profile'))
    if current_anatomy == 'unspecified':
        current_anatomy=normalize_anatomical_profile(getattr(profile, 'gender_presentation', None))
    profile.anatomical_profile=current_anatomy
    required=['face_shape','eye_color','hair_color','skin_tone','build']
""",
    """    traits=dict(profile.profile_json or {})
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
        profile.profile_json=traits
        profile.updated_at=datetime.utcnow()
        db.flush()
    required=['face_shape','eye_color','hair_color','skin_tone','build']
""",
)

replace_once(
    "app/services/image_prompt_engine.py",
    """    p = PartnerVisualProfile(user_id=user.id, partner_name=user.partner_name or 'Moones', fictional_age=_age_from_user(user), gender_presentation=presentation, ethnicity_or_regional_style='Iranian / Persian regional style, fictional person', face_description=face, hair_description=hair, eye_description=f'{traits[\"eye_shape\"]}, {traits[\"eye_color\"]}', skin_description=f'{traits[\"skin_tone\"]}, natural realistic skin texture', body_description=f'{traits[\"build\"]}, adult body proportions', height_impression=traits['height'], default_style='realistic candid smartphone photography', distinguishing_details=f'{traits[\"feature\"]}; {grooming}; no celebrity resemblance', default_city='Tehran', base_seed=seed, profile_json={**traits,'grooming':grooming,'interests': user.partner_interests or ''}, source='derived')
""",
    """    anatomical_profile = 'male' if presentation == 'masculine' else ('female' if presentation == 'feminine' else 'unspecified')
    p = PartnerVisualProfile(user_id=user.id, partner_name=user.partner_name or 'Moones', fictional_age=_age_from_user(user), gender_presentation=presentation, anatomical_profile=anatomical_profile, ethnicity_or_regional_style='Iranian / Persian regional style, fictional person', face_description=face, hair_description=hair, eye_description=f'{traits[\"eye_shape\"]}, {traits[\"eye_color\"]}', skin_description=f'{traits[\"skin_tone\"]}, natural realistic skin texture', body_description=f'{traits[\"build\"]}, adult body proportions', height_impression=traits['height'], default_style='realistic candid smartphone photography', distinguishing_details=f'{traits[\"feature\"]}; {grooming}; no celebrity resemblance', default_city='Tehran', base_seed=seed, profile_json={**traits,'grooming':grooming,'interests': user.partner_interests or '','anatomical_profile':anatomical_profile,'anatomical_profile_source':'derived_partner_gender'}, source='derived')
""",
)

replace_once(
    "app/api/telegram.py",
    """        \"image_parser_uncertain\": \"جزئیات عکس رو یک‌بار کامل بگو تا از نو برات بگیرم.\",
    }.get(reason)
""",
    """        \"image_parser_uncertain\": \"جزئیات عکس رو یک‌بار کامل بگو تا از نو برات بگیرم.\",
        \"anatomy_profile_missing\": \"پروفایل بدنی پارتنرت برای تصویر کاملاً برهنه هنوز کامل نشده؛ از ربات مدیریت مشخصات پارتنر رو بررسی کن و دوباره امتحان کن.\",
    }.get(reason)
""",
)

Path("tests/test_nude_anatomy_profile_migration.py").write_text(
    '''from types import SimpleNamespace\n\nfrom app.llm.image_client import VENICE_SEED_MIN\nfrom app.services.image_pipeline_v2 import (\n    anatomical_profile_source,\n    ensure_visual_profile_v2,\n    normalize_anatomical_profile,\n)\n\n\nclass DummyDB:\n    def __init__(self):\n        self.flush_count = 0\n\n    def flush(self):\n        self.flush_count += 1\n\n\ndef profile(**overrides):\n    values = dict(\n        profile_json={},\n        anatomical_profile=None,\n        gender_presentation=None,\n        base_seed=VENICE_SEED_MIN,\n        user_id=1,\n        version=3,\n        face_description=None,\n        hair_description=None,\n        eye_description=None,\n        skin_description=None,\n        body_description=None,\n        distinguishing_details=None,\n        updated_at=None,\n    )\n    values.update(overrides)\n    return SimpleNamespace(**values)\n\n\ndef test_anatomical_profile_normalizes_product_and_persian_gender_values():\n    assert normalize_anatomical_profile("feminine") == "female"\n    assert normalize_anatomical_profile("masculine") == "male"\n    assert normalize_anatomical_profile("دختر") == "female"\n    assert normalize_anatomical_profile("زن") == "female"\n    assert normalize_anatomical_profile("پسر") == "male"\n    assert normalize_anatomical_profile("مرد") == "male"\n    assert normalize_anatomical_profile("nonbinary") == "unspecified"\n\n\ndef test_existing_feminine_profile_is_migrated_from_user_partner_gender():\n    db = DummyDB()\n    user = SimpleNamespace(partner_gender="دختر")\n    visual = profile(gender_presentation="feminine")\n\n    result = ensure_visual_profile_v2(db, user, visual)\n\n    assert result.anatomical_profile == "female"\n    assert result.profile_json["anatomical_profile"] == "female"\n    assert result.profile_json["anatomical_profile_source"] == "inferred_user_partner_gender"\n    assert anatomical_profile_source(result) == "inferred_user_partner_gender"\n    assert db.flush_count >= 1\n\n\ndef test_existing_masculine_profile_falls_back_to_presentation_when_user_value_missing():\n    db = DummyDB()\n    user = SimpleNamespace(partner_gender=None)\n    visual = profile(gender_presentation="masculine")\n\n    result = ensure_visual_profile_v2(db, user, visual)\n\n    assert result.anatomical_profile == "male"\n    assert result.profile_json["anatomical_profile_source"] == "inferred_gender_presentation"\n\n\ndef test_explicit_anatomical_profile_is_never_overwritten_by_partner_gender():\n    db = DummyDB()\n    user = SimpleNamespace(partner_gender="دختر")\n    visual = profile(\n        anatomical_profile="intersex",\n        gender_presentation="feminine",\n        profile_json={"anatomical_profile": "intersex", "anatomical_profile_source": "explicit_profile"},\n    )\n\n    result = ensure_visual_profile_v2(db, user, visual)\n\n    assert result.anatomical_profile == "intersex"\n    assert result.profile_json["anatomical_profile_source"] == "explicit_profile"\n\n\ndef test_neutral_profile_remains_unspecified_without_explicit_anatomy():\n    db = DummyDB()\n    user = SimpleNamespace(partner_gender="nonbinary")\n    visual = profile(gender_presentation="neutral")\n\n    result = ensure_visual_profile_v2(db, user, visual)\n\n    assert result.anatomical_profile == "unspecified"\n''',
    encoding="utf-8",
)
