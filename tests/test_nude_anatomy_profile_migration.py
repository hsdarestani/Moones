from types import SimpleNamespace

from app.llm.image_client import VENICE_SEED_MIN
from app.services.image_pipeline_v2 import (
    PolicyDecision,
    SafetyDecision,
    anatomical_profile_source,
    construct_resolved_plan,
    ensure_visual_profile_v2,
    merge_image_intent,
    normalize_anatomical_profile,
    normalize_request_v2,
    parse_image_intent,
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
        partner_name="مونس",
        fictional_age=25,
        face_description="oval face",
        hair_description="dark shoulder-length hair",
        eye_description="dark almond eyes",
        skin_description="olive skin",
        body_description="average adult build",
        height_impression="average height",
        distinguishing_details="natural eyebrows",
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


def test_explicit_nude_plan_uses_migrated_anatomy_and_reaches_qa_contract():
    request = "یه عکس تمام قد کاملا لخت جلوی آینه توی اتاق بده"
    db = DummyDB()
    user = SimpleNamespace(partner_gender="دختر")
    visual = ensure_visual_profile_v2(db, user, profile(gender_presentation="feminine"))
    intent = parse_image_intent(normalize_request_v2(request))
    merged = merge_image_intent(intent, recent_context=[], memory_context=[], routine_context={})

    plan = construct_resolved_plan(
        intent,
        merged,
        SafetyDecision(PolicyDecision.ALLOW),
        visual,
        message_id=101,
        user_request=request,
    )

    assert plan.visual_requirements.explicit_nudity_requested is True
    assert plan.visual_requirements.anatomical_profile == "female"
    assert plan.visual_requirements.anatomy_consistency_required is True
    assert plan.visual_requirements.anatomy_qa_required is True
