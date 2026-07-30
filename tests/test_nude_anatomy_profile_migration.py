from types import SimpleNamespace

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
