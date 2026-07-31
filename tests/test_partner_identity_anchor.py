from types import SimpleNamespace

from app.llm.image_client import adapt_provider_prompts
from app.models.image_generation import PartnerVisualProfile
from app.models.user import User
from app.services import image_pipeline_v2 as v2
from app.services import image_generation_service as generation_service
from app.services.partner_identity_anchor import (
    derive_identity_anchor,
    identity_anchor_fingerprint,
)


class _DB:
    def flush(self):
        return None


def _profile():
    traits = {
        "face_shape": "long oval face",
        "jaw": "soft tapered jaw",
        "eyebrow_shape": "straight full eyebrows",
        "eye_shape": "almond eyes",
        "eye_color": "dark brown eyes",
        "nose": "straight medium nose",
        "feature": "small beauty mark near cheek",
        "hair_color": "deep brown hair",
        "hair_texture": "soft wavy hair",
        "hair_style": "long layered styled hair",
        "skin_tone": "light olive skin",
        "build": "average healthy build",
        "height": "average height impression",
        "anatomical_profile": "female",
        "anatomical_profile_source": "explicit_profile",
    }
    return PartnerVisualProfile(
        user_id=41,
        version=3,
        partner_name="Mahnaz",
        fictional_age=31,
        gender_presentation="feminine",
        anatomical_profile="female",
        face_description="long oval face, soft tapered jaw, straight full eyebrows, straight medium nose",
        hair_description="deep brown hair, soft wavy hair, long layered styled hair",
        eye_description="almond eyes, dark brown eyes",
        skin_description="light olive skin, natural realistic skin texture",
        body_description="average healthy build, adult body proportions",
        height_impression="average height impression",
        distinguishing_details="small beauty mark near cheek; no celebrity resemblance",
        base_seed=561991214,
        profile_json=traits,
        source="derived",
    )


def test_age_edit_updates_overlay_without_replacing_identity_anchor_or_seed():
    user = User(id=41, telegram_id=4141, partner_name="Mahnaz", partner_gender="female", partner_age_range="30+")
    profile = _profile()

    v2.ensure_visual_profile_v2(_DB(), user, profile)
    anchor_before = derive_identity_anchor(profile)
    fp_before = identity_anchor_fingerprint(profile)
    seed_before = profile.base_seed
    descriptor_before = v2.identity_descriptor_v2(profile)

    user.partner_age_range = "18-20"
    v2.ensure_visual_profile_v2(_DB(), user, profile)
    anchor_after = derive_identity_anchor(profile)
    fp_after = identity_anchor_fingerprint(profile)
    descriptor_after = v2.identity_descriptor_v2(profile)

    assert profile.fictional_age == 18
    assert descriptor_before["fictional_age"] == 30
    assert descriptor_after["fictional_age"] == 18
    assert profile.base_seed == seed_before == 561991214
    assert anchor_after == anchor_before
    assert fp_after == fp_before
    assert profile.profile_json["identity_anchor_fingerprint"] == fp_before
    assert profile.profile_json["mutable_profile_overlays"]["fictional_age"] == 18


def test_identity_fingerprint_is_independent_from_age_overlay():
    profile = _profile()
    v2.ensure_visual_profile_v2(_DB(), User(id=41, telegram_id=4141, partner_age_range="30+"), profile)
    fp_before = identity_anchor_fingerprint(profile)
    profile.fictional_age = 18
    assert identity_anchor_fingerprint(profile) == fp_before


def _executable_string_constants(fn) -> str:
    # co_consts[0] is the function docstring; intentionally ignore prose and
    # inspect only executable string constants used by the implementation.
    return " ".join(
        str(value).lower()
        for value in fn.__code__.co_consts[1:]
        if isinstance(value, str)
    )


def test_freeform_context_does_not_mutate_identity_and_is_not_scenario_hardcoded():
    profile = _profile()
    user = User(id=41, telegram_id=4141, partner_age_range="18-20")
    v2.ensure_visual_profile_v2(_DB(), user, profile)

    def plan_for(detail: str, message_id: int):
        intent = v2.ImageRequestIntent(is_image_request=True)
        intent.passthrough_visual_details = [detail]
        intent.parse_coverage.passthrough_visual_spans = [detail]
        merged = v2.merge_image_intent(intent)
        return v2.construct_resolved_plan(
            intent,
            merged,
            v2.SafetyDecision(),
            profile,
            message_id=message_id,
            user_request=detail,
        )

    first_detail = "inside a bioluminescent archive tunnel beneath amber glass ribs"
    second_detail = "on a windswept basalt observatory terrace beside a kinetic sculpture"
    first = plan_for(first_detail, 1)
    second = plan_for(second_detail, 2)

    assert first.identity["identity_fingerprint"] == second.identity["identity_fingerprint"]
    assert first_detail in first.passthrough_visual_details
    assert second_detail in second.passthrough_visual_details
    assert first_detail in v2.compile_image_prompt(first).positive_prompt
    assert second_detail in v2.compile_image_prompt(second).positive_prompt

    # Generic fallback code may call the semantic parser, but must not carry an
    # executable product-maintained alias list for particular scenarios.
    worker_constants = _executable_string_constants(generation_service.partner_identity_generation_required)
    for literal in ("cafe", "coffee", "park", "mirror", "bed", "bathroom", "کافه", "پارک", "آینه", "تخت"):
        assert literal not in worker_constants


def test_compiled_prompt_separates_canonical_identity_from_mutable_age():
    profile = _profile()
    user = User(id=41, telegram_id=4141, partner_age_range="18-20")
    v2.ensure_visual_profile_v2(_DB(), user, profile)
    intent = v2.ImageRequestIntent(is_image_request=True)
    intent.passthrough_visual_details = ["standing beside an abstract suspended paper installation"]
    intent.parse_coverage.passthrough_visual_spans = list(intent.passthrough_visual_details)
    plan = v2.construct_resolved_plan(intent, v2.merge_image_intent(intent), v2.SafetyDecision(), profile, message_id=9, user_request=intent.passthrough_visual_details[0])
    prompt = v2.compile_image_prompt(plan).positive_prompt.lower()

    assert "canonical identity lock" in prompt
    assert "mutable profile overlay" in prompt
    assert "fictional age 18" in prompt
    assert "must never redesign or replace the canonical identity" in prompt


def test_normal_partner_photos_use_identity_locked_krea_plan_not_scene_seed():
    partner_meta = {
        "expected_subject_count": 1,
        "primary_subject_role": "moones_partner",
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
    }
    assert generation_service.partner_identity_generation_required(partner_meta) is True
    assert generation_service.build_generation_attempt_plan(
        ["krea-2-turbo", "seedream-v5-lite"],
        adult_generation=False,
        identity_locked_generation=True,
        max_attempts=3,
    ) == [
        ("krea-2-turbo", 0),
        ("krea-2-turbo", 1),
        ("seedream-v5-lite", 0),
    ]

    object_meta = {
        "expected_subject_count": 0,
        "visual_requirements": {
            "partner_visible": False,
            "photo_contract": {"primary_subject": "object", "partner_visible": False},
        },
    }
    assert generation_service.partner_identity_generation_required(object_meta) is False


def test_krea_provider_compaction_keeps_age_overlay_and_canonical_face_lock():
    identity = (
        "Canonical identity lock: preserve the exact same recognizable fictional person across every request. "
        "Keep core face geometry, eye shape and spacing, eyebrow structure, nose geometry and jaw/chin structure anchored. "
        "Mutable profile overlay: render this same canonical person at fictional age 18. "
        "An age edit changes age appearance only; it must never redesign or replace the canonical identity. "
    )
    oversized = identity + ("Rich arbitrary user context with physically plausible details. " * 150)
    compact, _, diagnostics = adapt_provider_prompts("krea-2-turbo", oversized, "watermark, duplicate person")
    lowered = compact.lower()
    assert diagnostics["provider_prompt_compacted"] is True
    assert "canonical identity" in lowered
    assert "fictional age 18" in lowered
    assert "core face geometry" in lowered or "face shape" in lowered
