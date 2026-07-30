from pathlib import Path


def replace_section(path: str, start_marker: str, end_marker: str, replacement: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"start marker not found in {path}: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"end marker not found in {path}: {end_marker!r}")
    p.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_section(
    "app/services/image_pipeline_v2.py",
    "ANATOMICAL_PROFILE_VALUES={'male','female','intersex','unspecified'}",
    "def ensure_visual_profile_v2",
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

pipeline = Path("app/services/image_pipeline_v2.py")
text = pipeline.read_text(encoding="utf-8")
func_start = text.index("def ensure_visual_profile_v2")
block_start = text.index("    traits=dict(profile.profile_json or {})", func_start)
block_end = text.index("    required=['face_shape','eye_color','hair_color','skin_tone','build']", block_start)
new_block = """    traits=dict(profile.profile_json or {})
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
"""
pipeline.write_text(text[:block_start] + new_block + text[block_end:], encoding="utf-8")

replace_once(
    "app/api/telegram.py",
    '        "image_parser_uncertain": "جزئیات عکس رو یک‌بار کامل بگو تا از نو برات بگیرم.",\n',
    '        "image_parser_uncertain": "جزئیات عکس رو یک‌بار کامل بگو تا از نو برات بگیرم.",\n'
    '        "anatomy_profile_missing": "پروفایل بدنی پارتنرت برای تصویر کاملاً برهنه هنوز کامل نشده؛ از ربات مدیریت مشخصات پارتنر رو بررسی کن و دوباره امتحان کن.",\n',
)

Path("tests/test_nude_anatomy_profile_migration.py").write_text(
    '''from types import SimpleNamespace

from app.llm.image_client import VENICE_SEED_MIN
from app.services.image_pipeline_v2 import (
    anatomical_profile_source,
    ensure_visual_profile_v2,
    normalize_anatomical_profile,
)


class DummyDB:
    def __init__(self):
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1


def profile(**overrides):
    values = dict(
        profile_json={},
        anatomical_profile=None,
        gender_presentation=None,
        base_seed=VENICE_SEED_MIN,
        user_id=1,
        version=3,
        face_description=None,
        hair_description=None,
        eye_description=None,
        skin_description=None,
        body_description=None,
        distinguishing_details=None,
        updated_at=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_anatomical_profile_normalizes_product_and_persian_gender_values():
    assert normalize_anatomical_profile("feminine") == "female"
    assert normalize_anatomical_profile("masculine") == "male"
    assert normalize_anatomical_profile("دختر") == "female"
    assert normalize_anatomical_profile("زن") == "female"
    assert normalize_anatomical_profile("پسر") == "male"
    assert normalize_anatomical_profile("مرد") == "male"
    assert normalize_anatomical_profile("nonbinary") == "unspecified"


def test_existing_feminine_profile_is_migrated_from_user_partner_gender():
    db = DummyDB()
    user = SimpleNamespace(partner_gender="دختر")
    visual = profile(gender_presentation="feminine")

    result = ensure_visual_profile_v2(db, user, visual)

    assert result.anatomical_profile == "female"
    assert result.profile_json["anatomical_profile"] == "female"
    assert result.profile_json["anatomical_profile_source"] == "inferred_user_partner_gender"
    assert anatomical_profile_source(result) == "inferred_user_partner_gender"
    assert db.flush_count >= 1


def test_existing_masculine_profile_falls_back_to_presentation_when_user_value_missing():
    db = DummyDB()
    user = SimpleNamespace(partner_gender=None)
    visual = profile(gender_presentation="masculine")

    result = ensure_visual_profile_v2(db, user, visual)

    assert result.anatomical_profile == "male"
    assert result.profile_json["anatomical_profile_source"] == "inferred_gender_presentation"


def test_explicit_anatomical_profile_is_never_overwritten_by_partner_gender():
    db = DummyDB()
    user = SimpleNamespace(partner_gender="دختر")
    visual = profile(
        anatomical_profile="intersex",
        gender_presentation="feminine",
        profile_json={"anatomical_profile": "intersex", "anatomical_profile_source": "explicit_profile"},
    )

    result = ensure_visual_profile_v2(db, user, visual)

    assert result.anatomical_profile == "intersex"
    assert result.profile_json["anatomical_profile_source"] == "explicit_profile"


def test_neutral_profile_remains_unspecified_without_explicit_anatomy():
    db = DummyDB()
    user = SimpleNamespace(partner_gender="nonbinary")
    visual = profile(gender_presentation="neutral")

    result = ensure_visual_profile_v2(db, user, visual)

    assert result.anatomical_profile == "unspecified"
''',
    encoding="utf-8",
)
