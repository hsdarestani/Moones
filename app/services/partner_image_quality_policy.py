from __future__ import annotations

"""Quality policy for recurring-partner image generation.

Product-level guarantees kept here:

* the recurring partner should look naturally attractive and photogenic without
  losing her canonical identity or turning into a beauty-filter / doll face;
* follow-up photos visually compare against the prior accepted partner photo so
  face drift cannot pass on text descriptors alone;
* a fresh scene must not inherit stale props/actions from a prior image;
* scene-critical requests need an independent fail-closed visual scene check so
  a semantically similar but physically wrong setting (for example a street when
  a rooftop was requested) can never be delivered just because a reviewer says
  the scene is broadly compatible.
"""

import asyncio
import json
import logging
import re
from io import BytesIO
from typing import Any, Awaitable, Callable

from PIL import Image, ImageOps

from app.llm.vision_client import analyze_image_bytes_with_venice


logger = logging.getLogger(__name__)

STRICT_SCENE_REVIEW_MODEL = "z-ai-glm-5v-turbo"
STRICT_SCENE_REVIEW_TIMEOUT_SECONDS = 45.0
STRICT_IDENTITY_REVIEW_MODEL = "z-ai-glm-5v-turbo"
STRICT_IDENTITY_REVIEW_TIMEOUT_SECONDS = 45.0

PARTNER_FACE_QUALITY_POSITIVE = (
    "Facial quality directive: preserve the exact recurring fictional partner identity and make the "
    "same person consistently naturally beautiful, warm, expressive, and photogenic without changing "
    "who she is. Keep stable face geometry, eye shape and spacing, brows, nose, lips, jaw and chin from "
    "the canonical identity. Render lively dark eyes, a relaxed believable expression, harmonious adult "
    "facial proportions, soft but distinctive features, healthy realistic skin with pores and tiny natural "
    "imperfections, subtle human asymmetry, believable hairline and flyaways, and flattering but realistic "
    "lighting. The result should feel like a genuinely attractive real person caught in a good candid photo, "
    "not a generic AI model. Never redesign the face between scenes. Avoid beauty-filter, influencer-template, "
    "doll, waxy, plastic, mannequin, over-retouched, harsh, tired, lifeless, or uncanny facial rendering."
)

PARTNER_FACE_QUALITY_NEGATIVE = (
    "generic AI face, identity drift, different person, uncanny face, lifeless eyes, dull expression, "
    "distorted facial proportions, harsh under-eye shadows, waxy face, plastic face, doll face, mannequin face, "
    "over-smoothed skin, heavy beauty filter, exaggerated lips, exaggerated cosmetic features, low-detail face"
)

FRESH_SCENE_CONTINUITY_DIRECTIVE = (
    "Fresh-scene continuity rule: preserve the person, not stale scene contents. When the current request "
    "specifies a new scene, activity, pose, camera setup, or props, those current details replace prior-image "
    "scene state. Do not carry over an old held object, book, cup, table activity, support surface, pose, or "
    "foreground prop unless the current request explicitly asks for it."
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


def _explicit_current_scene(plan: Any) -> bool:
    current = _get(plan, "current_intent", {}) or {}
    scene = current.get("scene") if isinstance(current, dict) else None
    if isinstance(scene, dict) and scene.get("explicit_current_request"):
        return bool(scene.get("scene_key") or scene.get("location"))
    resolved_scene = _get(plan, "scene")
    return bool(_get(resolved_scene, "explicit_current_request", False))


def apply_partner_face_quality(compiled: Any, plan: Any) -> Any:
    """Add stable identity/face quality plus an anti-stale-scene directive."""
    if compiled is None or not _partner_visible_in_plan(plan):
        return compiled

    positive = str(getattr(compiled, "positive_prompt", "") or "")
    negative = str(getattr(compiled, "negative_prompt", "") or "")
    if PARTNER_FACE_QUALITY_POSITIVE not in positive:
        positive = f"{positive.rstrip()} {PARTNER_FACE_QUALITY_POSITIVE}".strip()
    if PARTNER_FACE_QUALITY_NEGATIVE not in negative:
        negative = f"{negative.rstrip()}, {PARTNER_FACE_QUALITY_NEGATIVE}".strip(", ")
    if _explicit_current_scene(plan) and FRESH_SCENE_CONTINUITY_DIRECTIVE not in positive:
        positive = f"{positive.rstrip()} {FRESH_SCENE_CONTINUITY_DIRECTIVE}".strip()
        negative = (
            f"{negative.rstrip()}, stale previous-scene prop, stale previous activity, unrequested book, "
            "unrequested cup, inherited foreground object"
        ).strip(", ")
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


def _scene_text(contract: dict) -> str:
    return " ".join(
        [*(contract.get("required_scene_elements") or []), contract.get("scene_context_summary") or ""]
    ).lower().replace("‌", " ")


def _rooftop_requested(contract: dict) -> bool:
    text = _scene_text(contract)
    return bool(
        re.search(
            r"\b(rooftop|roof top|roof|terrace)\b|پشت\s*بام|پشت\s*بوم|بام\s+ساختمان",
            text,
            re.IGNORECASE,
        )
    )


def _rooftop_payload_has_hard_evidence(payload: dict) -> bool:
    detected = str(payload.get("detected_scene") or "").lower()
    required = [str(v).lower() for v in (payload.get("required_scene_evidence") or [])]
    contradictory = [str(v).lower() for v in (payload.get("contradictory_scene_evidence") or [])]
    combined_contradiction = " ".join([detected, *contradictory])
    if re.search(r"street|sidewalk|pavement|road|roadside|ground[- ]?level|plaza|boulevard", combined_contradiction):
        return False

    evidence = " ".join(required)
    structural = re.search(
        r"rooftop|roof surface|roof deck|terrace|parapet|roof edge|rooftop fixture|roof boundary|roof railing",
        evidence,
    )
    return bool(structural)


def _strict_scene_prompt(visual_requirements: dict | None) -> str:
    contract = _scene_contract(visual_requirements)
    return (
        "You are an independent fail-closed scene verifier for a generated fictional-person photo. "
        "Return one JSON object only and do not identify any real person. Judge the physical setting "
        "from visible pixels, not from the request wording, clothing, mood, or generic city lights. "
        "The requested scene passes only when distinctive structural/environmental evidence is visibly "
        "present. A merely compatible background is not enough. In particular, a rooftop request MUST "
        "show visible rooftop structure: a roof/terrace surface plus a parapet, roof edge, rooftop railing, "
        "or rooftop fixture. City lights, skyline, streetlights, or an urban night background alone are NOT "
        "rooftop evidence. A street, pavement, sidewalk, roadside, plaza, boulevard, or ground-level urban "
        "scene is NOT a rooftop even when tall buildings or city lights are visible. Likewise, for any requested "
        "location, reject a visually different setting rather than inferring the requested place from props alone. "
        "List only concrete visible evidence. Required contract: "
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

    contract = _scene_contract(visual_requirements)
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
    rooftop_hard_evidence = (
        _rooftop_payload_has_hard_evidence(payload) if _rooftop_requested(contract) else True
    )
    passed = bool(
        scene_matches
        and confidence in {"medium", "high"}
        and required_evidence
        and not contradictory_evidence
        and rooftop_hard_evidence
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
        "IMAGE_PARTNER_STRICT_SCENE_GUARD_REJECTED detected_scene=%s required_evidence=%s contradictory_evidence=%s confidence=%s rooftop_hard_evidence=%s",
        payload.get("detected_scene"),
        required_evidence,
        contradictory_evidence,
        confidence,
        rooftop_hard_evidence,
    )
    return _mark_scene_guard_failure(qa, payload=payload)


def _identity_comparison_image(reference_bytes: bytes, candidate_bytes: bytes) -> bytes:
    """Build one side-by-side image so the existing one-image Vision transport can compare identities."""
    with Image.open(BytesIO(reference_bytes)) as left_raw, Image.open(BytesIO(candidate_bytes)) as right_raw:
        left = ImageOps.exif_transpose(left_raw).convert("RGB")
        right = ImageOps.exif_transpose(right_raw).convert("RGB")
        target_h = 768
        def resize(im: Image.Image) -> Image.Image:
            ratio = target_h / max(1, im.height)
            width = max(1, int(im.width * ratio))
            return im.resize((width, target_h))
        left = resize(left)
        right = resize(right)
        canvas = Image.new("RGB", (left.width + right.width, target_h))
        canvas.paste(left, (0, 0))
        canvas.paste(right, (left.width, 0))
        out = BytesIO()
        canvas.save(out, format="JPEG", quality=90, optimize=True)
        return out.getvalue()


def _mark_identity_guard_failure(qa: Any, payload: dict | None = None, *, uncertain: bool = False) -> Any:
    codes = list(getattr(qa, "reason_codes", None) or [])
    additions = ["qa_uncertain"] if uncertain else ["identity_inconsistent"]
    qa.reason_codes = list(dict.fromkeys(codes + additions))
    qa.passed = False
    setattr(qa, "strict_identity_guard_passed", False)
    if payload is not None:
        setattr(qa, "strict_identity_guard_payload", payload)
    return qa


async def enforce_strict_partner_identity_guard(
    reference_bytes: bytes | None,
    candidate_bytes: bytes | None,
    qa: Any,
    *,
    analyzer: Callable[..., Awaitable[dict]] | None = None,
) -> Any:
    """Compare a follow-up candidate against the prior accepted visual identity."""
    if qa is None or not getattr(qa, "passed", False):
        return qa
    if not reference_bytes:
        return qa
    if not candidate_bytes:
        return _mark_identity_guard_failure(qa, uncertain=True)
    try:
        comparison = _identity_comparison_image(reference_bytes, candidate_bytes)
    except Exception as exc:
        logger.warning("IMAGE_PARTNER_IDENTITY_COMPOSITE_FAILED error_type=%s", type(exc).__name__)
        return _mark_identity_guard_failure(qa, uncertain=True)

    prompt = (
        "You are a fail-closed identity-continuity verifier for a recurring fictional adult partner. "
        "The LEFT image is the previously accepted reference photo. The RIGHT image is the new candidate. "
        "Do not identify any real person and do not infer sensitive traits. Ignore clothing, scene, pose, camera, "
        "lighting, expression, hairstyle arrangement, and makeup changes. Compare identity-bearing facial cues: "
        "overall face geometry, eye shape/spacing, eyebrow structure, nose geometry, mouth/lip structure, jaw/chin, "
        "hair color/texture family, skin-tone family, and stable distinguishing details. The candidate passes only if "
        "it is reasonably the same fictional person despite normal photographic variation. If it looks like a different "
        "woman/person, fail it. Return JSON only. Schema: {\"same_identity\":true,\"confidence\":\"high\","
        "\"matching_identity_cues\":[\"eye spacing\",\"nose geometry\",\"jaw/chin\"],"
        "\"conflicting_identity_cues\":[]}"
    )
    analyze = analyzer or analyze_image_bytes_with_venice
    try:
        payload = await asyncio.wait_for(
            analyze(comparison, prompt=prompt, model=STRICT_IDENTITY_REVIEW_MODEL),
            timeout=STRICT_IDENTITY_REVIEW_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning("IMAGE_PARTNER_STRICT_IDENTITY_GUARD_FAILED error_type=%s", type(exc).__name__)
        return _mark_identity_guard_failure(qa, uncertain=True)
    if not isinstance(payload, dict):
        return _mark_identity_guard_failure(qa, uncertain=True)

    confidence = str(payload.get("confidence") or "low").lower()
    matching = [str(v).strip() for v in (payload.get("matching_identity_cues") or []) if str(v).strip()]
    conflicting = [str(v).strip() for v in (payload.get("conflicting_identity_cues") or []) if str(v).strip()]
    passed = bool(
        payload.get("same_identity") is True
        and confidence in {"medium", "high"}
        and len(matching) >= 2
        and not conflicting
    )
    setattr(qa, "strict_identity_guard_payload", payload)
    setattr(qa, "strict_identity_guard_passed", passed)
    if passed:
        logger.info("IMAGE_PARTNER_STRICT_IDENTITY_GUARD_OK matching=%s confidence=%s", matching, confidence)
        return qa
    logger.info(
        "IMAGE_PARTNER_STRICT_IDENTITY_GUARD_REJECTED matching=%s conflicting=%s confidence=%s",
        matching,
        conflicting,
        confidence,
    )
    return _mark_identity_guard_failure(qa, payload)
