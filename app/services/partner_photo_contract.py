from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


_CAMERA_ALIASES = {
    "selfie": "casual_selfie",
    "phone_selfie": "casual_selfie",
    "casual_selfie": "casual_selfie",
    "mirror": "mirror_selfie",
    "mirror_selfie": "mirror_selfie",
    "tripod": "tripod_timer",
    "timer": "tripod_timer",
    "tripod_timer": "tripod_timer",
    "camera_placed": "tripod_timer",
    "object_photo": "point_of_view",
    "pov": "point_of_view",
    "point_of_view": "point_of_view",
    "passenger_pov": "passenger_pov",
    "dashboard_mount": "dashboard_mount",
    "candid": "candid",
    "casual_phone_photo": "casual_phone_photo",
}

_PRIMARY_SUBJECT_ALIASES = {
    "partner": "partner",
    "person": "partner",
    "self": "partner",
    "pet": "pet",
    "animal": "pet",
    "object": "object",
    "drink": "object",
    "food": "object",
    "scene": "scene",
    "environment": "scene",
}


@dataclass
class PartnerPhotoContract:
    request_type: str = "new_photo"
    primary_subject: str = "partner"
    partner_visible: bool = True
    pet_visible: bool = False
    object_only: bool = False
    pet_only: bool = False
    hands_only: bool = False
    face_visible: bool | None = None
    face_hidden: bool = False
    back_to_camera: bool = False
    camera_mode: str = "casual_phone_photo"
    framing: str = "natural_medium_or_medium_wide"
    required_body_regions: list[str] = field(default_factory=list)
    forbidden_body_regions: list[str] = field(default_factory=list)
    visible_objects: list[str] = field(default_factory=list)
    held_objects: list[str] = field(default_factory=list)
    natural_capture_required: bool = True
    realism_constraints: list[str] = field(default_factory=lambda: [
        "photorealistic",
        "natural skin texture",
        "plausible camera placement",
        "natural body posture",
        "ordinary phone-camera realism",
    ])
    identity_visibility_scope: str = "full"
    expected_human_subject_count: int = 1
    world_memory_context: list[str] = field(default_factory=list)
    identity_consistency_required: bool = True
    current_scene_from_chat: bool = False
    scene_context_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _unique(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def normalize_camera_mode(value: Any) -> str | None:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _CAMERA_ALIASES.get(normalized, normalized or None)


def build_partner_photo_contract(visual_intent: Any) -> dict[str, Any]:
    """Build one authoritative photo contract from the model's structured output.

    This function deliberately does not infer intent from Persian keywords. The semantic model
    decides the request; this layer only normalizes and resolves internal consistency.
    """
    request_type = str(_value(visual_intent, "request_type", "new_photo") or "new_photo").strip().lower()
    visible_objects = _unique(_value(visual_intent, "visible_objects", []) or [])
    object_only = bool(_value(visual_intent, "object_only", False))
    pet_only = bool(_value(visual_intent, "pet_only", False))
    hands_only = bool(_value(visual_intent, "hands_only", False))
    raw_primary_subject = _value(visual_intent, "primary_subject", None)
    if raw_primary_subject not in (None, ""):
        normalized_primary = str(raw_primary_subject).strip().lower()
        primary_subject = _PRIMARY_SUBJECT_ALIASES.get(normalized_primary, normalized_primary)
    elif pet_only or request_type in {"pet_photo", "pet"}:
        primary_subject = "pet"
    elif object_only or request_type in {"object_photo", "object", "scene_photo"}:
        primary_subject = "scene" if request_type == "scene_photo" else "object"
    else:
        primary_subject = "partner"
    if primary_subject in {"object", "scene"} and not hands_only:
        object_only = True
    pet_visible = bool(_value(visual_intent, "pet_visible", False) or pet_only or primary_subject == "pet")
    partner_visible_value = _bool_or_none(_value(visual_intent, "partner_visible", None))
    partner_visible = True if partner_visible_value is None else partner_visible_value

    if (object_only or primary_subject in {"object", "scene"}) and not hands_only:
        primary_subject = "object" if primary_subject != "scene" else "scene"
        partner_visible = False
    if pet_only:
        primary_subject = "pet"
        partner_visible = False
        pet_visible = True

    face_visible = _bool_or_none(_value(visual_intent, "face_visible", None))
    face_hidden = bool(_value(visual_intent, "face_hidden", False))
    back_to_camera = bool(_value(visual_intent, "back_to_camera", False))
    if hands_only or back_to_camera:
        face_hidden = True if face_visible is not True else face_hidden
    if not partner_visible:
        face_visible = False
        face_hidden = True

    framing = str(_value(visual_intent, "framing", None) or "").strip().lower()
    camera = normalize_camera_mode(_value(visual_intent, "camera_mode", None) or _value(visual_intent, "camera", None))
    if hands_only or object_only or pet_only:
        camera = camera or "point_of_view"
        framing = framing or "detail"
    elif back_to_camera:
        camera = camera or "tripod_timer"
        framing = framing or "natural_medium_or_medium_wide"
    elif framing == "full_body" and camera in {None, "casual_selfie"}:
        # A true arm-length full-body selfie is usually implausible. A mirror or timer is natural.
        camera = "mirror_selfie"
    else:
        camera = camera or ("casual_selfie" if primary_subject == "partner" and partner_visible else "casual_phone_photo")
        framing = framing or "natural_medium_or_medium_wide"
    if primary_subject == "partner" and partner_visible and camera in {"casual_selfie", "mirror_selfie"} and face_visible is None and not face_hidden:
        face_visible = True

    required_regions = _unique([
        *(_value(visual_intent, "required_body_regions", []) or []),
        *(_value(visual_intent, "body_or_face_regions", []) or []),
    ])
    forbidden_regions = _unique(_value(visual_intent, "forbidden_body_regions", []) or [])
    if hands_only and "hands" not in required_regions:
        required_regions.append("hands")
    if face_hidden and "face" not in forbidden_regions:
        forbidden_regions.append("face")

    expected_humans = 1 if partner_visible else 0
    identity_scope = "absent" if not partner_visible else ("partial" if hands_only or face_hidden or back_to_camera else "full")

    realism = _unique([
        "photorealistic",
        "natural skin texture",
        "plausible camera placement",
        "natural body posture",
        "ordinary phone-camera realism",
        *(_value(visual_intent, "realism_constraints", []) or []),
    ])

    return PartnerPhotoContract(
        request_type=request_type,
        primary_subject=primary_subject,
        partner_visible=partner_visible,
        pet_visible=pet_visible,
        object_only=object_only,
        pet_only=pet_only,
        hands_only=hands_only,
        face_visible=face_visible,
        face_hidden=face_hidden,
        back_to_camera=back_to_camera,
        camera_mode=camera,
        framing=framing,
        required_body_regions=required_regions,
        forbidden_body_regions=forbidden_regions,
        visible_objects=visible_objects,
        held_objects=_unique(_value(visual_intent, "held_objects", []) or []),
        natural_capture_required=bool(_value(visual_intent, "natural_capture_required", True)),
        realism_constraints=realism,
        identity_visibility_scope=identity_scope,
        expected_human_subject_count=expected_humans,
        identity_consistency_required=bool(_value(visual_intent, "identity_continuity_required", True) and partner_visible and identity_scope != "absent"),
        current_scene_from_chat=bool(_value(visual_intent, "current_scene_from_chat", False)),
        scene_context_summary=str(_value(visual_intent, "scene_context_summary", "") or "").strip() or None,
    ).to_dict()


def attach_world_memory_context(contract: dict[str, Any] | None, memories: Iterable[Any]) -> dict[str, Any]:
    contract = dict(contract or {})
    primary_subject = contract.get("primary_subject")
    allowed_types = {
        "pet",
        "partner_pet",
        "partner_world",
        "visual_scene_state",
        "visual_identity",
        "partner_profile",
        "recurring_place",
        "recurring_object",
    }
    selected: list[str] = []
    for memory in memories or []:
        memory_type = str(getattr(memory, "type", "") or "").strip().lower()
        content = str(getattr(memory, "content", "") or "").strip()
        if not content:
            continue
        relevant_type = memory_type in allowed_types or "visual" in memory_type or "pet" in memory_type
        if relevant_type or primary_subject in {"pet", "object", "scene"}:
            selected.append(content[:500])
        if len(selected) >= 4:
            break
    contract["world_memory_context"] = _unique(selected)
    return contract


def prompt_constraints(contract: dict[str, Any] | None) -> list[str]:
    contract = contract or {}
    lines: list[str] = []
    expected = int(contract.get("expected_human_subject_count", 1))
    primary = contract.get("primary_subject", "partner")

    if expected == 0:
        lines.append("No human person is visible anywhere in the frame; do not invent a stranger, model, photographer, face, reflection, or body.")
    if primary == "pet":
        lines.append("The established pet is the primary subject of the photo. Render the pet naturally and consistently with relevant relationship memory.")
    elif primary == "object":
        lines.append("The requested object, drink, food, or personal item is the primary subject; compose the image around it rather than a portrait.")
    elif primary == "scene":
        lines.append("The requested environment is the primary subject; do not turn the image into a centered portrait.")

    if contract.get("hands_only"):
        lines.append("Show only the partner's natural hands or forearms interacting with the requested object. Do not show the face, head, torso, or a full person.")
    if contract.get("face_hidden"):
        lines.append("The partner's face must remain fully outside the frame or naturally obscured; no recognizable face, reflected face, or accidental headshot.")
    elif contract.get("face_visible") is True:
        lines.append("The partner's recognizable face must be naturally visible and consistent with the stored identity.")
    if contract.get("back_to_camera"):
        lines.append("The partner is turned away from the camera with a natural back-facing pose; do not rotate them into a front-facing ID portrait.")

    camera = contract.get("camera_mode")
    camera_lines = {
        "casual_selfie": "Camera logic: the image viewpoint originates from the phone lens held naturally at arm length. The phone device itself is outside the frame in a non-mirror selfie; no external photographer, overhead camera, or third-person viewpoint. Keep visible environmental context and avoid a biometric headshot.",
        "casual_phone_photo": "Camera logic: a believable spontaneous phone photo taken in the moment, with natural perspective and slight everyday imperfection.",
        "mirror_selfie": "Camera logic: a believable mirror selfie photographed through the mirror. The phone may be visible only as part of the same-subject mirror geometry; no external or overhead third-person camera and any reflection must be the same subject.",
        "tripod_timer": "Camera logic: the phone or camera was placed on a stable surface and triggered by timer; no visible photographer and no impossible selfie arm.",
        "point_of_view": "Camera logic: first-person point-of-view toward the requested object/pet/scene; no centered face portrait unless explicitly requested.",
        "passenger_pov": "Camera logic: believable passenger-side point-of-view inside the vehicle; the driver remains safely positioned and is not taking a handheld selfie while driving.",
        "dashboard_mount": "Camera logic: believable dashboard-mounted or fixed in-car camera; hands and driving posture remain safe and physically plausible.",
        "candid": "Camera logic: a believable candid moment with natural posture and no studio posing.",
    }
    if camera in camera_lines:
        lines.append(camera_lines[camera])

    if contract.get("natural_capture_required", True):
        lines.append("The result must look like a real personal photo from an ongoing relationship, not a passport photo, casting headshot, studio catalogue image, or generic AI portrait.")
    if contract.get("required_body_regions"):
        lines.append("Required visible body regions: " + ", ".join(contract["required_body_regions"]) + ".")
    if contract.get("forbidden_body_regions"):
        lines.append("Must not visibly reveal these regions: " + ", ".join(contract["forbidden_body_regions"]) + ".")
    if contract.get("visible_objects"):
        lines.append("Required visible objects: " + ", ".join(contract["visible_objects"]) + ".")
    if contract.get("held_objects"):
        lines.append("Objects naturally held or touched: " + ", ".join(contract["held_objects"]) + ".")
    if contract.get("current_scene_from_chat") and contract.get("scene_context_summary"):
        lines.append("Current-moment continuity is mandatory: keep the visible setting, support surface and activity consistent with the latest stated partner context: " + str(contract["scene_context_summary"]) + ".")
    if contract.get("identity_consistency_required"):
        lines.append("Identity continuity is mandatory: this must be the same recurring fictional partner, never a new generic person.")
    if contract.get("world_memory_context"):
        lines.append("Relevant established partner-world memory, use only when applicable and never invent conflicting details: " + " | ".join(contract["world_memory_context"]) + ".")
    if contract.get("realism_constraints"):
        lines.append("Realism constraints: " + ", ".join(contract["realism_constraints"]) + ".")
    return lines


def image_acknowledgement(metadata: dict[str, Any] | None) -> str:
    metadata = metadata or {}
    vr = metadata.get("visual_requirements") or {}
    contract = vr.get("photo_contract") or metadata.get("photo_contract") or {}
    primary = contract.get("primary_subject")
    content = str(metadata.get("content_classification") or "")
    if primary in {"pet", "object", "scene"}:
        return "باشه، یه لحظه ازش یه عکس خوب برات می‌گیرم 🤍"
    if "nudity" in content or metadata.get("adult_intent"):
        return "باشه... یه لحظه، همون‌جوری که گفتی برات عکس می‌گیرم 🤍"
    return "باشه، یه لحظه یه عکس خوب برات می‌گیرم 🤍"


def image_status_text(status: str | None, error_code: str | None = None) -> str | None:
    if status == "queued":
        return "آره یادمه؛ یه لحظه بذار عکسش خوب دربیاد 🤍"
    if status in {"processing", "generating"}:
        return "هنوز دارم درستش می‌کنم؛ این یکی رو نمی‌خوام سرسری بفرستم 🤍"
    if status == "sending":
        return "آماده‌ست، الان می‌فرستمش 🤍"
    if status == "delivery_failed":
        return "عکس آماده شد ولی ارسالش گیر کرد؛ دوباره می‌فرستمش."
    if status == "sent":
        return "فرستادمش 🤍 یکم بالاتر توی چت می‌بینیش."
    if status == "failed":
        if error_code == "image_quality_single_subject_failed":
            return "این یکی طبیعی و شبیه چیزی که خواستی درنیومد؛ نفرستادمش و سکه‌ات برگشت 🤍"
        return "این بار عکس درست درنیومد؛ اگه سکه‌ای رزرو شده بود برگشته."
    return None
