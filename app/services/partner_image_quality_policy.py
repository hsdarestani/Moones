from __future__ import annotations

"""Quality policy for recurring-partner image generation.

This module keeps two product-level rules separate from the generic image worker:

* the recurring partner should look naturally attractive and photogenic without
  losing her canonical identity or turning into a beauty-filter / doll face;
* scene-critical requests need an independent fail-closed visual scene check so
  a semantically similar but physically wrong setting (for example a street when
  a rooftop was requested) can never be delivered just because the first QA
  reviewer accepted it.
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from app.llm.vision_client import analyze_image_bytes_with_venice


logger = logging.getLogger(__name__)

STRICT_SCENE_REVIEW_MODEL = "z-ai-glm-5v-turbo"
STRICT_SCENE_REVIEW_TIMEOUT_SECONDS = 45.0

PARTNER_FACE_QUALITY_POSITIVE = (
    "Facial quality directive: preserve the exact recurring fictional partner identity, "
    "while rendering her naturally attractive and photogenic with harmonious adult facial "
    "proportions, expressive lively eyes, balanced soft features, natural eyebrows, realistic "
    "lips, healthy realistic skin texture with visible pores and subtle imperfections, a relaxed "
    "believable expression, and subtle human asymmetry. Keep her recognizably the same person; "
    "improve rendering quality, not identity. Avoid a beauty-filter, influencer-template, doll, "
    "waxy, plastic, or over-retouched face."
)

PARTNER_FACE_QUALITY_NEGATIVE = (
    "unattractive face, uncanny face, lifeless eyes, dull expression, distorted facial proportions, "
    "waxy face, plastic face, doll face, over-smoothed skin, heavy beauty filter, exaggerated lips, "
    "exaggerated cosmetic features, low-detail face"
)


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _partner_visible_in_plan(plan: Any) -> bool:
    requirements = _get(plan, "visual_requirements")
    if requirements is not None:
        if _get(requirements, "partner_visible", True) is False:
            return False
        if bool(_get(requirements, "object_only", False) or _get(requirements, "pet_only", False)):
            return False
    identity = _get(plan, "identity")
    return bool(identity)


def apply_partner_face_quality(compiled: Any, plan: Any) -> Any:
    """Add a stable natural-attractiveness rendering target for visible partners.

    The identity anchor remains authoritative. This changes rendering quality and
    expression rather than re-randomizing the face, so continuity is preserved.
    """
    if compiled is None or not _partner_visible_in_plan(plan):
        return compiled

    positive = str(getattr(compiled, "positive_prompt", "") or "")
    negative = str(getattr(compiled, "negative_prompt", "") or "")
    if PARTNER_FACE_QUALITY_POSITIVE not in positive:
        positive = f"{positive.rstrip()} {PARTNER_FACE_QUALITY_POSITIVE}".strip()
    if PARTNER_FACE_QUALITY_NEGATIVE not in negative:
        negative = f"{negative.rstrip()}, {PARTNER_FACE_QUALITY_NEGATIVE}".strip(", ")
    compiled.positive_prompt = positive
    compiled.negative_prompt = negative
    return compiled


def _scene_contract(visual_requirements: dict | None) -> dict:
    requirements = dict(visual_requirements or {})
    must = dict(requirements.get("must_satisfy") or {})
    photo_contract = dict(requirements.get("photo_contract") or {})
    visibility_targets = dict(requirements.get("visibility_targets") or {})
    required_scene_elements = [
        str(value).strip()
        for value in (must.get("required_scene_elements") or [])
        if str(value).strip()
    ]
    return {
        "required_scene_elements": required_scene_elements,
        "scene_context_summary": str(photo_contract.get("scene_context_summary") or "").strip(),
        "current_scene_from_chat": bool(photo_contract.get("current_scene_from_chat")),
        "environment_visibility_required": bool(
            requirements.get("environment_visibility_required")
            or visibility_targets.get("environment_visible")
            or photo_contract.get("current_scene_from_chat")
        ),
    }


def strict_scene_review_required(visual_requirements: dict | None) -> bool:
    contract = _scene_contract(visual_requirements)
    return bool(
        contract["environment_visibility_required"]
        and (contract["required_scene_elements"] or contract["scene_context_summary"])
    )


def _strict_scene_prompt(visual_requirements: dict | None) -> str:
    contract = _scene_contract(visual_requirements)
    return (
        "You are an independent fail-closed scene verifier for a generated fictional-person photo. "
        "Return one JSON object only and do not identify any real person. Judge the physical setting "
        "from visible pixels, not from the request wording, clothing, mood, or generic city lights. "
        "The requested scene passes only when distinctive structural/environmental evidence is visibly "
        "present. A merely compatible background is not enough. In particular, a rooftop request MUST "
        "show clear rooftop/elevated-building evidence such as a roof or terrace surface, parapet/roof "
        "edge, rooftop fixtures, or an unmistakably elevated vantage point; a street, pavement, sidewalk, "
        "roadside, plaza, or ground-level urban scene is NOT a rooftop even when city lights are visible. "
        "Likewise, for any requested location, reject a visually different setting rather than inferring "
        "the requested place from props alone. List concrete visual evidence. Required contract: "
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
        + '\nSchema: {"scene_matches_request":false,"detected_scene":"street/sidewalk at ground level",'
        '"required_scene_evidence":[],"contradictory_scene_evidence":["sidewalk","road"],'
        '"confidence":"high"}'
    )


def _mark_scene_guard_failure(qa: Any, *, payload: dict | None = None, uncertain: bool = False) -> Any:
    codes = list(getattr(qa, "reason_codes", None) or [])
    additions = ["qa_uncertain"] if uncertain else ["requested_scene_not_visible", "wrong_scene"]
    qa.reason_codes = list(dict.fromkeys(codes + additions))
    qa.passed = False
    if not uncertain:
        setattr(qa, "requested_scene_visible", False)
    setattr(qa, "strict_scene_guard_passed", False)
    if payload is not None:
        setattr(qa, "strict_scene_guard_payload", payload)
    return qa


async def enforce_strict_partner_scene_guard(
    image_bytes: bytes | None,
    qa: Any,
    *,
    visual_requirements: dict | None,
    analyzer: Callable[..., Awaitable[dict]] | None = None,
) -> Any:
    """Require a second independent scene opinion after the ordinary QA passes."""
    if qa is None or not getattr(qa, "passed", False):
        return qa
    if not strict_scene_review_required(visual_requirements):
        return qa
    if not image_bytes:
        logger.warning("IMAGE_PARTNER_STRICT_SCENE_GUARD_NO_IMAGE")
        return _mark_scene_guard_failure(qa, uncertain=True)

    analyze = analyzer or analyze_image_bytes_with_venice
    try:
        payload = await asyncio.wait_for(
            analyze(
                image_bytes,
                prompt=_strict_scene_prompt(visual_requirements),
                model=STRICT_SCENE_REVIEW_MODEL,
            ),
            timeout=STRICT_SCENE_REVIEW_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "IMAGE_PARTNER_STRICT_SCENE_GUARD_FAILED error_type=%s",
            type(exc).__name__,
        )
        return _mark_scene_guard_failure(qa, uncertain=True)

    if not isinstance(payload, dict):
        return _mark_scene_guard_failure(qa, uncertain=True)

    confidence = str(payload.get("confidence") or "low").strip().lower()
    scene_matches = payload.get("scene_matches_request") is True
    required_evidence = [
        str(value).strip()
        for value in (payload.get("required_scene_evidence") or [])
        if str(value).strip()
    ]
    contradictory_evidence = [
        str(value).strip()
        for value in (payload.get("contradictory_scene_evidence") or [])
        if str(value).strip()
    ]
    passed = bool(
        scene_matches
        and confidence in {"medium", "high"}
        and required_evidence
        and not contradictory_evidence
    )

    setattr(qa, "strict_scene_guard_payload", payload)
    setattr(qa, "strict_scene_guard_passed", passed)
    if passed:
        logger.info(
            "IMAGE_PARTNER_STRICT_SCENE_GUARD_OK detected_scene=%s evidence=%s confidence=%s",
            payload.get("detected_scene"),
            required_evidence,
            confidence,
        )
        return qa

    logger.info(
        "IMAGE_PARTNER_STRICT_SCENE_GUARD_REJECTED detected_scene=%s required_evidence=%s contradictory_evidence=%s confidence=%s",
        payload.get("detected_scene"),
        required_evidence,
        contradictory_evidence,
        confidence,
    )
    return _mark_scene_guard_failure(qa, payload=payload)
