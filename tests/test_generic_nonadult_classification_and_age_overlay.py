from types import SimpleNamespace

from app.services import image_generation_runtime  # installs runtime policies
from app.services import image_pipeline_v2 as v2
from app.llm import image_client
from app.llm.image_client import VENICE_SEED_MIN


class DummyDB:
    def flush(self):
        pass


def _profile(age=18):
    user = SimpleNamespace(
        partner_gender="دختر",
        partner_name="مهناز",
        partner_age_range="18-20" if age <= 20 else "30+",
    )
    profile = SimpleNamespace(
        profile_json={},
        anatomical_profile="female",
        gender_presentation="feminine",
        base_seed=VENICE_SEED_MIN,
        user_id=1,
        version=3,
        partner_name="مهناز",
        fictional_age=age,
        face_description="oval face, softly defined jawline, natural facial proportions",
        hair_description="dark shoulder-length hair with a natural hairline",
        eye_description="dark almond-shaped eyes and natural eyebrows",
        skin_description="olive skin with natural texture",
        body_description="average adult feminine build with natural proportions",
        height_impression="average height",
        distinguishing_details="natural eyebrows",
        updated_at=None,
    )
    return user, v2.ensure_visual_profile_v2(DummyDB(), user, profile)


def _compile(text, age=18):
    user, profile = _profile(age)
    intent = v2.parse_image_intent(v2.normalize_request_v2(text))
    merged = v2.merge_image_intent(
        intent,
        recent_context=[],
        memory_context=[],
        routine_context={},
    )
    plan = v2.construct_resolved_plan(
        intent,
        merged,
        v2.SafetyDecision(),
        profile,
        message_id=9001,
        user_request=text,
    )
    return intent, plan, v2.compile_image_prompt(plan)


def _provider_prompt(compiled, model="krea-2-turbo"):
    positive, negative, metadata = image_client.adapt_provider_prompts(
        model,
        compiled.positive_prompt,
        compiled.negative_prompt,
    )
    return positive, negative, metadata


def test_hair_visibility_does_not_make_arbitrary_scene_suggestive():
    text = (
        "حالا یه عکس تمام‌قد از خودت روی پشت‌بوم یه ساختمون شب، "
        "باد موهاتو به‌هم زده، لباس مشکی رسمی پوشیدی و چراغ‌های شهر پشت سرت معلومه."
    )
    intent, _, compiled = _compile(text, age=18)
    assert str(intent.content_classification) == str(v2.ContentClassification.NORMAL)
    assert intent.adult_intent is None
    assert "hair" in intent.body_visibility.regions
    assert "anatomical profile" not in compiled.positive_prompt.lower()
    assert "contradictory anatomy" not in compiled.negative_prompt.lower()


def test_other_ordinary_regions_do_not_escalate_to_suggestive():
    samples = [
        "یه عکس بده موهات توی باد معلوم باشه",
        "یه عکس بده دستت روی میز معلوم باشه",
        "یه عکس بده صورتت کامل معلوم باشه",
        "یه عکس بده چشمات واضح معلوم باشه",
        "یه عکس بده بازوهات توی کادر باشه",
    ]
    for text in samples:
        intent = v2.parse_image_intent(v2.normalize_request_v2(text))
        assert str(intent.content_classification) == str(v2.ContentClassification.NORMAL), text


def test_adult_sensitive_region_still_escalates():
    intent = v2.parse_image_intent(v2.normalize_request_v2("یه عکس بده سینه‌هات معلوم باشه"))
    assert str(intent.content_classification) != str(v2.ContentClassification.NORMAL)


def test_explicit_adult_intent_is_never_downgraded():
    intent = v2.parse_image_intent(v2.normalize_request_v2("یه عکس کاملاً لخت از خودت بده"))
    assert str(intent.content_classification) == str(v2.ContentClassification.FULL_NUDITY)
    assert intent.adult_intent == "full_nudity"


def test_age_18_stays_exact_in_plan_and_compiled_contract_but_not_provider_payload():
    text = (
        "یه عکس از خودت بده که وسط یه کتاب‌فروشی قدیمی بین قفسه‌ها ایستادی، "
        "یه پلیور طوسی گشاد پوشیدی و داری یه کتاب رو ورق می‌زنی."
    )
    _, plan, compiled = _compile(text, age=18)
    assert plan.identity["descriptor"]["fictional_age"] == 18
    assert "fictional age 18" in compiled.positive_prompt.lower()

    provider_positive, _, metadata = _provider_prompt(compiled)
    provider_lower = provider_positive.lower()
    assert "fictional age 18" not in provider_lower
    assert "fictional_age=18" not in provider_lower
    assert "very young adult appearance" in provider_lower
    assert "clearly adult" in provider_lower
    assert metadata["provider_profile_age_sanitized"] is True


def test_age_edit_changes_provider_age_band_without_changing_internal_storage_contract():
    text = "یه عکس طبیعی از خودت بده کنار پنجره ایستادی و نور روز روی صورتته"
    _, young_plan, young = _compile(text, age=18)
    _, older_plan, older = _compile(text, age=34)

    # ensure_visual_profile_v2 normalizes the configured 30+ range to age 30.
    assert young_plan.identity["descriptor"]["fictional_age"] == 18
    assert older_plan.identity["descriptor"]["fictional_age"] == 30

    young_provider, _, young_meta = _provider_prompt(young)
    older_provider, _, older_meta = _provider_prompt(older)
    assert "very young adult appearance" in young_provider.lower()
    assert "adult appearance in the thirties" in older_provider.lower()
    assert young_meta["provider_profile_age_sanitized"] is True
    assert older_meta["provider_profile_age_sanitized"] is True


def test_provider_age_sanitizer_does_not_rewrite_arbitrary_user_numbers():
    prompt = "Scene detail: apartment number 18, shelf 18, no system fictional-age field."
    adapted, _, metadata = image_client.adapt_provider_prompts(
        "krea-2-turbo",
        prompt,
        "",
    )
    assert "number 18" in adapted
    assert "shelf 18" in adapted
    assert metadata["provider_profile_age_sanitized"] is False
