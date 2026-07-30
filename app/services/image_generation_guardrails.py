from __future__ import annotations

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
    "chest": "breasts",
    "breasts": "breasts",
    "full_body": "full_body",
    "face": "face",
}

_PUBLIC_PRIVACY_VALUES = {"public", "public_outdoor", "public_indoor", "street", "cafe", "park"}


def _normalize_fa_text(value: object) -> str:
    return " ".join(str(value or "").replace("‌", " ").replace("ي", "ی").replace("ك", "ک").lower().split())


@dataclass(frozen=True)
class AdultScenePolicyResult:
    routine_context: dict[str, Any] | None
    private_scene_applied: bool = False
    denied_reason: str | None = None


def canonical_body_region(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _BODY_REGION_ALIASES.get(normalized, normalized)


def apply_semantic_safety_contract(intent, visual_intent, safety_signals: dict[str, Any] | None = None):
    """Transfer model-extracted safety fields into the validated V2 intent."""
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
        intent.content_classification = v2.ContentClassification.FULL_NUDITY
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
        region = intent.body_visibility.regions.setdefault("breasts", v2.BodyRegionIntent())
        region.mentioned = True
        region.visibility_requested = True
        region.framing_requested = True
        region.explicit_current_request = True
    elif nudity_level == "lingerie":
        intent.content_classification = v2.ContentClassification.LINGERIE
        intent.adult_intent = "lingerie"
    elif nudity_level == "suggestive":
        intent.content_classification = v2.ContentClassification.SUGGESTIVE
        intent.adult_intent = "suggestive"
    return intent


def apply_deterministic_adult_visual_intent(intent, user_text: str):
    """Preserve explicit Persian adult visibility requests when semantic extraction under-classifies them."""
    from app.services import image_pipeline_v2 as v2

    text = _normalize_fa_text(user_text)
    full_nudity = any(term in text for term in ("لخت", "لختی", "برهنه", "کاملا برهنه", "کاملاً برهنه", "بدون لباس", "لباس نداشته", "لباس نپوش"))
    breast_term = any(term in text for term in ("ممه", "ممه ها", "ممه هات", "سینه", "سینه هات", "پستان"))
    visibility = any(term in text for term in ("عکس", "بده", "بدی", "بفرست", "بفرس", "ببینم", "نشون", "نشان", "معلوم باش", "پیدا باش", "میخوام", "می خوام"))

    if full_nudity and visibility:
        intent.content_classification = v2.ContentClassification.FULL_NUDITY
        intent.adult_intent = "full_nudity"
        for region_name in ("breasts", "buttocks", "full_body"):
            region = intent.body_visibility.regions.setdefault(region_name, v2.BodyRegionIntent())
            region.mentioned = True
            region.visibility_requested = True
            region.explicit_current_request = True
            if region_name == "full_body":
                region.framing_requested = True
    elif breast_term and visibility:
        intent.content_classification = v2.ContentClassification.TOPLESS
        intent.adult_intent = "topless"
        region = intent.body_visibility.regions.setdefault("breasts", v2.BodyRegionIntent())
        region.mentioned = True
        region.visibility_requested = True
        region.framing_requested = True
        region.explicit_current_request = True
    return intent


def inherit_recent_adult_visual_intent(intent, user_text: str, recent_conversation):
    """Carry an immediately preceding explicit adult request into a body-referential photo follow-up.

    This is intentionally narrow: ordinary requests such as «عکس قدی بده» never inherit nudity.
    """
    from app.services import image_pipeline_v2 as v2

    if str(intent.content_classification) in {
        str(v2.ContentClassification.TOPLESS),
        str(v2.ContentClassification.FULL_NUDITY),
    }:
        return intent
    text = _normalize_fa_text(user_text)
    delivery = any(term in text for term in ("عکس", "سلفی", "بده", "بدی", "بفرست", "بفرس", "بگیر", "ببینم"))
    body_followup = any(term in text for term in ("همه جات", "همه جاتو", "همه هات", "همه هاتو", "کل بدنت", "بدنتو", "بدنت رو", "سرتاپات"))
    if not (delivery and body_followup):
        return intent
    for message in reversed(list(recent_conversation or [])[-12:]):
        if str(getattr(message, "role", "") or "") != "user":
            continue
        prior_text = _normalize_fa_text(getattr(message, "content", ""))
        if prior_text == text:
            continue
        if any(term in prior_text for term in ("لخت", "لختی", "برهنه", "بدون لباس", "ممه", "سینه")):
            inherited = apply_deterministic_adult_visual_intent(intent, prior_text)
            if str(inherited.content_classification) in {
                str(v2.ContentClassification.TOPLESS),
                str(v2.ContentClassification.FULL_NUDITY),
            }:
                return inherited
    return intent


def apply_adult_scene_policy(intent, routine_context: dict[str, Any] | None) -> AdultScenePolicyResult:
    """Keep topless and full-nudity generation in a private indoor setting."""
    from app.services import image_pipeline_v2 as v2

    adult_classifications = {
        str(v2.ContentClassification.TOPLESS),
        str(v2.ContentClassification.FULL_NUDITY),
    }
    if str(intent.content_classification) not in adult_classifications:
        return AdultScenePolicyResult(routine_context=routine_context)

    explicit_scene = bool(
        intent.scene.explicit_current_request
        and (intent.scene.scene_key or intent.scene.location or intent.scene.environment_type)
    )
    privacy = str(intent.scene.privacy or "").strip().lower()
    environment = str(intent.scene.environment_type or "").strip().lower()
    scene_values = {
        str(intent.scene.scene_key or "").strip().lower(),
        str(intent.scene.location or "").strip().lower(),
        environment,
        privacy,
    }
    explicitly_private = explicit_scene and privacy == "private" and not (scene_values & _PUBLIC_PRIVACY_VALUES)
    if explicitly_private:
        return AdultScenePolicyResult(routine_context=routine_context)

    intent.scene.scene_key = "private_indoor"
    intent.scene.location = "private indoor setting"
    intent.scene.environment_type = "private_indoor"
    intent.scene.privacy = "private"
    intent.scene.required_visible_environment_elements = ["private indoor environment"]
    # A prior cafe/street contract must never survive a private adult-scene override.
    contract = dict(getattr(intent, "photo_contract", {}) or {})
    contract["current_scene_from_chat"] = False
    contract["scene_context_summary"] = None
    intent.photo_contract = contract
    stale_scene_prefixes = (
        "current scene and activity:",
        "keep the photo in the partner's semantically resolved current location",
    )
    intent.passthrough_visual_details = [
        item for item in list(getattr(intent, "passthrough_visual_details", []) or [])
        if not _normalize_fa_text(item).startswith(stale_scene_prefixes)
    ]
    if getattr(intent, "parse_coverage", None) is not None:
        intent.parse_coverage.passthrough_visual_spans = [
            item for item in list(intent.parse_coverage.passthrough_visual_spans or [])
            if not _normalize_fa_text(item).startswith(stale_scene_prefixes)
        ]
    safe_routine = dict(routine_context or {})
    safe_routine["location"] = None
    safe_routine["scene"] = None
    safe_routine["environment_type"] = None
    return AdultScenePolicyResult(routine_context=safe_routine, private_scene_applied=True)


def select_generation_model(*, content_classification: object, default_model: str, adult_model: str | None) -> str:
    from app.services import image_pipeline_v2 as v2

    if str(content_classification) in {str(v2.ContentClassification.TOPLESS), str(v2.ContentClassification.FULL_NUDITY)} and str(adult_model or "").strip():
        return str(adult_model).strip()
    return default_model
