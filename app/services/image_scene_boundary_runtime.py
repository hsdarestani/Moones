from __future__ import annotations

"""Runtime hardening for explicit fresh image scenes.

This module closes an integration gap between the deterministic Persian parser,
the semantic VisualIntent adapter, and V2 context merging. A current request that
explicitly names a new setting must replace stale visual state even when that
setting is free-form and has no entry in the small compatibility SCENES table.
"""

import logging
from typing import Any

from app.services import image_generation_service as _generation
from app.services import image_pipeline_v2 as _v2


logger = logging.getLogger(__name__)

_STALE_PROVENANCE = {
    str(_v2.Provenance.SOURCE_PLAN),
    str(_v2.Provenance.RECENT),
    str(_v2.Provenance.ROUTINE),
    str(_v2.Provenance.MEMORY),
}
_SCENE_COUPLED_FIELDS = {
    "activity",
    "pose",
    "support_surface",
    "camera",
    "framing",
    "held_objects",
    "visible_objects",
}
_SCENE_IDENTITY_FIELDS = {
    "scene": "scene_key",
    "location": "location",
    "environment_type": "environment_type",
}


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _scene_boundary(intent: Any) -> bool:
    scene = getattr(intent, "scene", None)
    if scene is None or not bool(getattr(scene, "explicit_current_request", False)):
        return False
    contract = dict(getattr(intent, "photo_contract", {}) or {})
    return bool(
        getattr(scene, "scene_key", None)
        or getattr(scene, "location", None)
        or getattr(scene, "environment_type", None)
        or list(getattr(scene, "required_visible_environment_elements", None) or [])
        or contract.get("explicit_scene_boundary")
    )


def _clear_if_stale(merged: dict, name: str) -> None:
    field = merged.get(name)
    if not isinstance(field, _v2.ResolvedField):
        return
    if bool(getattr(field, "explicit_current_request", False)):
        return
    if str(getattr(field, "source", "") or "") not in _STALE_PROVENANCE:
        return
    merged[name] = _v2.ResolvedField(None, _v2.Provenance.SYSTEM)
    logger.info(
        "IMAGE_EXPLICIT_SCENE_STALE_FIELD_DROPPED field=%s old_source=%s",
        name,
        getattr(field, "source", None),
    )


# ---------------------------------------------------------------------------
# Deterministic parser: a scene token found in the current message is explicit.
# ---------------------------------------------------------------------------
_existing_parse = _v2.parse_image_intent
if getattr(_existing_parse, "_moones_explicit_scene_boundary_safe", False):
    _original_parse = getattr(_existing_parse, "_moones_original_explicit_scene_parse", _existing_parse)
else:
    _original_parse = _existing_parse


def _parse_with_explicit_scene(request):
    intent = _original_parse(request)
    scene = getattr(intent, "scene", None)
    if scene is not None and (getattr(scene, "scene_key", None) or getattr(scene, "location", None)):
        scene.explicit_current_request = True
    return intent


_parse_with_explicit_scene._moones_explicit_scene_boundary_safe = True
_parse_with_explicit_scene._moones_original_explicit_scene_parse = _original_parse
_v2.parse_image_intent = _parse_with_explicit_scene


# ---------------------------------------------------------------------------
# Semantic adapter: honor the schema's scene_explicit_current_request flag.
# ---------------------------------------------------------------------------
_existing_semantic_adapter = _generation.apply_semantic_visual_intent_to_v2_intent
if getattr(_existing_semantic_adapter, "_moones_explicit_scene_boundary_safe", False):
    _original_semantic_adapter = getattr(
        _existing_semantic_adapter,
        "_moones_original_explicit_scene_adapter",
        _existing_semantic_adapter,
    )
else:
    _original_semantic_adapter = _existing_semantic_adapter


def _semantic_adapter_with_explicit_scene(intent, semantic_decision, *, resolved_visual_intent=None):
    visual = resolved_visual_intent or getattr(semantic_decision, "visual_intent", None)
    result = _original_semantic_adapter(
        intent,
        semantic_decision,
        resolved_visual_intent=resolved_visual_intent,
    )
    if visual is None:
        return result

    contract = dict(getattr(result, "photo_contract", {}) or {})
    current_scene_from_chat = bool(contract.get("current_scene_from_chat"))
    explicit = bool(_value(visual, "scene_explicit_current_request", False))
    has_current_scene_evidence = bool(
        _value(visual, "scene", None)
        or _value(visual, "location", None)
        or _value(visual, "environment_type", None)
        or list(_value(visual, "required_visible_environment_elements", []) or [])
    )
    # These semantic fields describe the current message unless the model has
    # explicitly marked them as inherited current-world context.
    if not current_scene_from_chat and has_current_scene_evidence:
        explicit = True

    if explicit:
        result.scene.explicit_current_request = True
        contract["explicit_scene_boundary"] = True
        result.photo_contract = contract
        logger.info(
            "IMAGE_SEMANTIC_EXPLICIT_SCENE_BOUNDARY scene=%s location=%s environment_type=%s",
            getattr(result.scene, "scene_key", None),
            getattr(result.scene, "location", None),
            getattr(result.scene, "environment_type", None),
        )
    return result


_semantic_adapter_with_explicit_scene._moones_explicit_scene_boundary_safe = True
_semantic_adapter_with_explicit_scene._moones_original_explicit_scene_adapter = _original_semantic_adapter
_generation.apply_semantic_visual_intent_to_v2_intent = _semantic_adapter_with_explicit_scene


# ---------------------------------------------------------------------------
# Merge: fresh arbitrary scenes replace stale canonical scene state as well.
# ---------------------------------------------------------------------------
_existing_merge = _v2.merge_image_intent
if getattr(_existing_merge, "_moones_arbitrary_scene_reset_safe", False):
    _original_merge = getattr(_existing_merge, "_moones_original_arbitrary_scene_merge", _existing_merge)
else:
    _original_merge = _existing_merge


def _merge_with_arbitrary_scene_reset(
    current_intent,
    source_plan=None,
    recent_context=None,
    memory_context=None,
    routine_context=None,
):
    merged = _original_merge(
        current_intent,
        source_plan,
        recent_context=recent_context,
        memory_context=memory_context,
        routine_context=routine_context,
    )
    if not _scene_boundary(current_intent):
        return merged

    scene = current_intent.scene
    for name in _SCENE_COUPLED_FIELDS:
        _clear_if_stale(merged, name)

    # If the current arbitrary scene has no canonical value for one of these
    # fields, the old scene/location/environment must not survive underneath it.
    for merged_name, current_name in _SCENE_IDENTITY_FIELDS.items():
        if getattr(scene, current_name, None) in (None, "", [], {}):
            _clear_if_stale(merged, merged_name)
    return merged


_merge_with_arbitrary_scene_reset._moones_arbitrary_scene_reset_safe = True
_merge_with_arbitrary_scene_reset._moones_original_arbitrary_scene_merge = _original_merge
_v2.merge_image_intent = _merge_with_arbitrary_scene_reset


# ---------------------------------------------------------------------------
# Requirements: free-form explicit scenes are still hard environment contracts.
# ---------------------------------------------------------------------------
_existing_requirements = _v2.resolve_visual_requirements
if getattr(_existing_requirements, "_moones_arbitrary_scene_requirement_safe", False):
    _original_requirements = getattr(
        _existing_requirements,
        "_moones_original_arbitrary_scene_requirements",
        _existing_requirements,
    )
else:
    _original_requirements = _existing_requirements


def _requirements_with_arbitrary_scene(intent, *, user_request: str = "", previous_job=None):
    requirements = _original_requirements(
        intent,
        user_request=user_request,
        previous_job=previous_job,
    )
    if not _scene_boundary(intent):
        return requirements

    scene = intent.scene
    evidence = [
        value
        for value in [
            getattr(scene, "scene_key", None),
            getattr(scene, "location", None),
            getattr(scene, "environment_type", None),
            *(getattr(scene, "required_visible_environment_elements", None) or []),
        ]
        if value not in (None, "", [], {})
    ]
    evidence = list(dict.fromkeys(str(value).strip() for value in evidence if str(value).strip()))
    if evidence:
        requirements.environment_visibility_required = True
        requirements.visibility_targets.environment_visible = True
        must = dict(requirements.must_satisfy or {})
        must["required_scene_elements"] = list(
            dict.fromkeys(list(must.get("required_scene_elements") or []) + evidence)
        )
        requirements.must_satisfy = must
        if "environment_visibility_required" not in requirements.reason_codes:
            requirements.reason_codes.append("environment_visibility_required")
    return requirements


_requirements_with_arbitrary_scene._moones_arbitrary_scene_requirement_safe = True
_requirements_with_arbitrary_scene._moones_original_arbitrary_scene_requirements = _original_requirements
_v2.resolve_visual_requirements = _requirements_with_arbitrary_scene
