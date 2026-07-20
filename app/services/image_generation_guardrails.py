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
    "chest": "chest",
    "breasts": "chest",
    "full_body": "full_body",
    "face": "face",
}

_PUBLIC_PRIVACY_VALUES = {"public", "public_outdoor", "public_indoor", "street", "cafe", "park"}


@dataclass(frozen=True)
class AdultScenePolicyResult:
    routine_context: dict[str, Any] | None
    private_scene_applied: bool = False
    denied_reason: str | None = None


def canonical_body_region(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _BODY_REGION_ALIASES.get(normalized, normalized)


def apply_semantic_safety_contract(intent, visual_intent, safety_signals: dict[str, Any] | None = None):
    """Transfer model-extracted safety fields into the validated V2 intent.

    This is deliberately based on structured semantic fields, not Persian keyword matching.
    """
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
        intent.content_classification = v2.ContentClassification.UNSUPPORTED_EXPLICIT_VISIBILITY
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
    elif nudity_level == "lingerie":
        intent.content_classification = v2.ContentClassification.LINGERIE
        intent.adult_intent = "lingerie"
    elif nudity_level == "suggestive":
        intent.content_classification = v2.ContentClassification.SUGGESTIVE
        intent.adult_intent = "suggestive"
    return intent


def apply_adult_scene_policy(intent, routine_context: dict[str, Any] | None) -> AdultScenePolicyResult:
    """Keep allowed full nudity in a private setting unless the user explicitly chose one.

    The policy never invents furniture, pose, clothing, or lighting. It only prevents routine
    context (for example a street or cafe) from turning a context-free nude request into public nudity.
    """
    from app.services import image_pipeline_v2 as v2

    if str(intent.content_classification) != str(v2.ContentClassification.FULL_NUDITY):
        return AdultScenePolicyResult(routine_context=routine_context)

    explicit_scene = bool(
        intent.scene.explicit_current_request
        and (intent.scene.scene_key or intent.scene.location or intent.scene.environment_type)
    )
    privacy = str(intent.scene.privacy or "").strip().lower()
    environment = str(intent.scene.environment_type or "").strip().lower()

    if explicit_scene and (privacy in _PUBLIC_PRIVACY_VALUES or environment in _PUBLIC_PRIVACY_VALUES):
        return AdultScenePolicyResult(routine_context=routine_context, denied_reason="adult_public_scene_not_supported")

    if explicit_scene:
        return AdultScenePolicyResult(routine_context=routine_context)

    intent.scene.scene_key = "private_indoor"
    intent.scene.location = "private indoor setting"
    intent.scene.environment_type = "private_indoor"
    intent.scene.privacy = "private"
    intent.scene.required_visible_environment_elements = ["private indoor environment"]
    safe_routine = dict(routine_context or {})
    safe_routine["location"] = None
    return AdultScenePolicyResult(routine_context=safe_routine, private_scene_applied=True)


def select_generation_model(*, content_classification: object, default_model: str, adult_model: str | None) -> str:
    from app.services import image_pipeline_v2 as v2

    if str(content_classification) == str(v2.ContentClassification.FULL_NUDITY) and str(adult_model or "").strip():
        return str(adult_model).strip()
    return default_model
