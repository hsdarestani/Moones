from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message

from app.services.image_pipeline_v2 import ImageRouteDecisionV2

logger = logging.getLogger(__name__)

SEMANTIC_ROUTER_MODEL_PROVIDER = "venice"
SEMANTIC_ROUTER_MODEL = "qwen-3-6-plus"
SEMANTIC_ROUTER_SCHEMA_VERSION = "semantic-image-intent-v2-partner-photo"
SEMANTIC_ROUTER_ESTIMATED_LATENCY_MS = 900
SEMANTIC_ROUTER_ESTIMATED_COST_USD = 0.0007
IMAGE_CLARIFICATION_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class ResolvedImageRequest:
    action: str
    original_request_text: str | None = None
    clarification_answer_text: str | None = None
    effective_visual_intent: Any | None = None
    source_message_id: int | None = None
    request_chain_id: str | None = None
    resolution_reason: str = "pending_clarification_answer"


@dataclass(frozen=True)
class PendingImageClarificationResolution:
    message: Message
    action: str
    clarification_message: Message | None = None
    source_user_telegram_message_id: int | None = None
    source_user_message: Message | None = None
    effective_request_text: str | None = None
    effective_source_telegram_message_id: int | None = None
    resolved_request: ResolvedImageRequest | None = None


_CLARIFICATION_ANSWERS = {
    "generate_new": {"عکس جدید", "یه عکس جدید", "عکس تازه", "تازه", "جدید", "جدید بساز", "از اول بساز"},
    "refine_previous": {"تغییر عکس قبلی", "قبلی رو تغییر بده", "عکس قبلی رو ویرایش کن", "ادیت عکس قبلی", "همون قبلی رو درست کن"},
    "chat": {"فقط دارم درباره ش حرف می زنم", "فقط حرف می زنم", "عکس نمی خوام", "سوال بود", "منظورم گفتگو بود"},
}


def normalize_image_clarification_text(text: str) -> str:
    """Normalize harmless Persian orthographic differences without broad intent matching."""
    normalized = (text or "").strip().replace("\u200c", " ").replace("ي", "ی").replace("ى", "ی").replace("ك", "ک")
    for ch in "\n\t\r.,،؛;:!؟?…ـ":
        normalized = normalized.replace(ch, " ")
    return " ".join(normalized.split())


def _collapse_stretched_clarification_runs(text: str) -> str:
    """Collapse exaggerated runs of 3+ characters while preserving legitimate double letters."""
    if not text:
        return text
    result: list[str] = []
    index = 0
    while index < len(text):
        end = index + 1
        while end < len(text) and text[end] == text[index]:
            end += 1
        run = text[index:end]
        result.append(run[0] if len(run) >= 3 and run[0] != " " else run)
        index = end
    return "".join(result)

def _clarification_action_from_reply(text: str) -> str | None:
    """Resolve only short, choice-like replies to the newest pending clarification."""
    normalized = normalize_image_clarification_text(text)
    stretched = _collapse_stretched_clarification_runs(normalized)
    normalized_answers = {
        action: {normalize_image_clarification_text(answer) for answer in answers}
        for action, answers in _CLARIFICATION_ANSWERS.items()
    }
    for candidate in (normalized, stretched):
        exact = next((action for action, answers in normalized_answers.items() if candidate in answers), None)
        if exact:
            return exact

    words = [word for word in stretched.split() if word]
    if not words or len(words) > 8:
        return None
    word_set = set(words)
    generate_markers = {"تازه", "جدید", "دوباره", "بگیر", "بده", "بفرست", "بساز"}
    refine_markers = {"قبلی", "همون", "تغییر", "ویرایش", "ادیت", "عوض"}
    chat_markers = {"نمیخوام", "نمی", "سوال", "گفتگو", "حرف"}
    fillers = {
        "تازه", "جدید", "عکس", "یه", "یک", "بده", "بدین", "بگیر", "بگیری",
        "بساز", "برام", "لطفا", "لطفاً", "بابا", "کشتی", "دیگه", "حالا",
        "از", "اول", "همونو", "همون", "قبلی", "رو", "را", "تغییر", "ویرایش",
        "ادیت", "عوض", "کن", "بکن", "میخوام", "می", "خوام", "ببینم",
        "ببینمت", "نشونم", "نشانم", "لطفاً"
    }
    substantive = [word for word in words if word not in fillers]
    if word_set & chat_markers:
        return "chat" if len(substantive) <= 2 else None
    if word_set & generate_markers and not (word_set & refine_markers) and not substantive:
        return "generate_new"
    if word_set & refine_markers and not (word_set & generate_markers) and not substantive:
        return "refine_previous"
    return None


def default_pending_clarification_action(text: str) -> str | None:
    """Resolve one old new-vs-edit question without ever asking it a second time."""
    normalized = _collapse_stretched_clarification_runs(normalize_image_clarification_text(text))
    words = [word for word in normalized.split() if word]
    if not words or len(words) > 12:
        return None
    word_set = set(words)
    chat_markers = {"نه", "نمیخوام", "نمی", "بیخیال", "ولش", "وا", "چرا", "چی", "سوال", "حرف"}
    refine_markers = {"قبلی", "همون", "همونو", "تغییر", "ویرایش", "ادیت", "عوض"}
    generate_markers = {"تازه", "جدید", "دوباره", "بگیر", "بده", "بفرست", "بساز", "عکس", "ببینم", "ببینمت"}
    if word_set & chat_markers:
        return None
    if word_set & refine_markers:
        return "refine_previous"
    if word_set & generate_markers:
        return "generate_new"
    # The question already offered only new/edit. Any other short affirmative reply
    # defaults to a new photo instead of reopening the same clarification loop.
    return "generate_new" if len(words) <= 4 else None


def supersede_pending_image_clarification(
    db: Session,
    *,
    user_id: int,
    telegram_message_id: int | None = None,
    reason: str = "new_user_message",
) -> Message | None:
    """Close the newest unresolved clarification when the user moves on or gives a new request."""
    candidates = db.scalars(
        select(Message).where(
            Message.user_id == user_id,
            Message.role == "assistant",
            Message.input_type == "image_clarification",
        ).order_by(Message.created_at.desc(), Message.id.desc()).limit(20)
    ).all()
    for message in candidates:
        metadata = dict(message.metadata_json or {})
        if metadata.get("kind") != "pending_image_clarification":
            continue
        if metadata.get("status") == "pending":
            metadata.update({
                "status": "superseded",
                "superseded_at": datetime.utcnow().isoformat(),
                "superseded_reason": reason,
                "superseded_by_telegram_message_id": telegram_message_id,
            })
            message.metadata_json = metadata
            logger.info(
                "IMAGE_CLARIFICATION_SUPERSEDED user_id=%s clarification_id=%s reason=%s",
                user_id,
                message.id,
                reason,
            )
            return message
        return None
    return None


_NORMALIZED_CLARIFICATION_ANSWERS = {
    action: {normalize_image_clarification_text(answer) for answer in answers}
    for action, answers in _CLARIFICATION_ANSWERS.items()
}
STANDALONE_GENERATE_NEW_ANSWERS = {
    normalize_image_clarification_text(answer) for answer in ("عکس جدید", "یه عکس جدید", "عکس تازه")
}


def _norm_intent_text(text: str) -> str:
    normalized = normalize_image_clarification_text(text).lower()
    return " ".join(normalized.split())


def canonical_explicit_image_action(text: str) -> str | None:
    """Deterministic structured recovery for clear commands when the model is unavailable.

    This is not the primary router and does not use regex; production routing still
    asks the semantic model first except for exact clarification recovery.
    """
    t = _norm_intent_text(text)
    if not t:
        return None
    if t in {'چیشد','چی شد','پس چی شد','چی شد پس','هنوز نیومد','عکس کجاست','فرستادی','چرا طول کشید'}:
        return SemanticImageAction.STATUS_QUERY
    if t in {'بیخیال','لغوش کن','نمیخوامش','نمی خوامش','نفرست'}:
        return SemanticImageAction.CANCEL_PENDING
    chat_markers = ["چی میگی", "چی می گی", "چرا", "مصنوعی", "ممنوعه", "دوست ندارم", "توضیح بده", "درباره", "نمیخوام", "نمی خوام", "لازم نیست"]
    if any(x in t for x in chat_markers):
        return None
    exact_actions = {
        "عکس جدید": SemanticImageAction.GENERATE_NEW,
        "یه عکس جدید": SemanticImageAction.GENERATE_NEW,
        "عکس تازه": SemanticImageAction.GENERATE_NEW,
        "همون قبلی رو درست کن": SemanticImageAction.REFINE_PREVIOUS,
        "همون قبلی رو بهتر کن": SemanticImageAction.REFINE_PREVIOUS,
        "عکس قبلی رو درست کن": SemanticImageAction.REFINE_PREVIOUS,
        "عکس قبلی رو بهتر کن": SemanticImageAction.REFINE_PREVIOUS,
        "همون عکس رو بهتر کن": SemanticImageAction.REFINE_PREVIOUS,
        "یکی دیگه مثل قبلی": SemanticImageAction.VARIATION,
        "یکی دیگه شبیه قبلی": SemanticImageAction.VARIATION,
        "عکس قبلی رو دوباره بفرست": SemanticImageAction.RESEND_EXACT,
        "همون عکس رو دوباره بفرست": SemanticImageAction.RESEND_EXACT,
        "فقط حرف می زنم": SemanticImageAction.CHAT,
        "فقط دارم درباره ش حرف می زنم": SemanticImageAction.CHAT,
    }
    if t in exact_actions:
        return exact_actions[t]
    if ("قبلی" in t or "این بار" in t or "همون عکس" in t) and any(v in t for v in ["درست کن", "بهتر کن", "تغییر بده", "ویرایش کن", "ادیت کن", "عوض کن"]):
        return SemanticImageAction.REFINE_PREVIOUS
    if any(v in t for v in ["مثل قبلی", "شبیه قبلی", "همونجوری یکی دیگه", "مدل دیگه از همون"]):
        return SemanticImageAction.VARIATION
    if any(ref in t for ref in ["قبلی", "همونو", "همون رو", "همون عکس"]) and any(v in t for v in ["دوباره بفرست", "باز بفرست", "بفرست"]):
        return SemanticImageAction.RESEND_EXACT
    # Compatibility fallback only. Production still calls the semantic model for
    # GENERATE_NEW so this helper never becomes the source of an empty VisualIntent.
    wants_visual = "عکس" in t or "تصویر" in t or "ببینمت" in t or "نشونم بده" in t
    delivery = any(v in t for v in ["بده", "بدی", "بفرست", "بفرستی", "بساز", "درست کن", "ببینمت", "نشونم بده", "باشی"])
    if wants_visual and delivery:
        return SemanticImageAction.GENERATE_NEW
    return None

def canonical_standalone_image_action(text: str) -> str | None:
    return canonical_explicit_image_action(text)


def resolve_pending_image_clarification(
    db: Session, *, user_id: int, text: str, now: datetime | None = None
) -> PendingImageClarificationResolution | None:
    """Resolve the newest clarification once; never reopen a new-vs-edit loop."""
    action = _clarification_action_from_reply(text)
    now = now or datetime.utcnow()
    candidates = db.scalars(
        select(Message).where(
            Message.user_id == user_id,
            Message.role == "assistant",
            Message.input_type == "image_clarification",
        ).order_by(Message.created_at.desc(), Message.id.desc()).limit(20)
    ).all()
    for message in candidates:
        metadata = message.metadata_json or {}
        if metadata.get("kind") != "pending_image_clarification":
            continue
        # The newest clarification supersedes every older one, including after it is consumed.
        if metadata.get("status") != "pending":
            return None
        if not message.created_at or now - message.created_at > IMAGE_CLARIFICATION_TTL:
            return None
        if action is None:
            action = default_pending_clarification_action(text)
        if action is None:
            return None
        source_tid = metadata.get("source_user_telegram_message_id")
        source_message = None
        if source_tid is not None:
            source_message = db.scalar(select(Message).where(Message.user_id == user_id, Message.role == "user", Message.telegram_message_id == source_tid).order_by(Message.id.desc()).limit(1))
        if source_tid is not None and source_message is None:
            return PendingImageClarificationResolution(message=message, action=action, clarification_message=message, source_user_telegram_message_id=source_tid, effective_request_text=None, effective_source_telegram_message_id=source_tid)
        effective_text = source_message.content if source_message is not None else text
        resolved = ResolvedImageRequest(action=action, original_request_text=effective_text, clarification_answer_text=text, effective_visual_intent=metadata.get("effective_visual_intent") or metadata.get("visual_intent"), source_message_id=getattr(source_message, "id", None), request_chain_id=metadata.get("request_chain_id"), resolution_reason="pending_clarification_answer")
        return PendingImageClarificationResolution(message=message, action=action, clarification_message=message, source_user_telegram_message_id=source_tid, source_user_message=source_message, effective_request_text=effective_text, effective_source_telegram_message_id=source_tid, resolved_request=resolved)
    return None


def mark_image_clarification_resolved(
    resolution: PendingImageClarificationResolution, *, telegram_message_id: int, now: datetime | None = None
) -> None:
    metadata = dict(resolution.message.metadata_json or {})
    metadata.update({
        "status": "resolved",
        "resolved_action": resolution.action,
        "resolved_at": (now or datetime.utcnow()).isoformat(),
        "resolved_by_telegram_message_id": telegram_message_id,
    })
    resolution.message.metadata_json = metadata


class SemanticImageAction(StrEnum):
    CHAT = "chat"
    GENERATE_NEW = "generate_new"
    REFINE_PREVIOUS = "refine_previous"
    VARIATION = "variation"
    RESEND_EXACT = "resend_exact"
    CLARIFY = "clarify"
    STATUS_QUERY = "status_query"
    CANCEL_PENDING = "cancel_pending"


@dataclass
class VisualIntent:
    subject_focus: str | None = None
    body_or_face_regions: list[str] = field(default_factory=list)
    scene: str | None = None
    location: str | None = None
    environment_type: str | None = None
    privacy: str | None = None
    required_visible_environment_elements: list[str] = field(default_factory=list)
    scene_explicit_current_request: bool = False
    pose: str | None = None
    activity: str | None = None
    expression: str | None = None
    wardrobe: str | None = None
    visible_objects: list[str] = field(default_factory=list)
    held_objects: list[str] = field(default_factory=list)
    camera: str | None = None
    framing: str | None = None
    lighting: str | None = None
    exclusions: list[str] = field(default_factory=list)
    secondary_subject: str | None = None
    interaction: str | None = None
    expected_subject_count: int | None = None
    freeform_visual_constraints: list[str] = field(default_factory=list)
    confidence: float = 1.0
    gaze_direction: str | None = None
    eye_contact_required: bool = False
    nudity_level: str | None = None
    explicit_anatomy_focus: bool = False
    request_type: str | None = None
    primary_subject: str | None = None
    partner_visible: bool | None = None
    pet_visible: bool = False
    object_only: bool = False
    pet_only: bool = False
    hands_only: bool = False
    face_visible: bool | None = None
    face_hidden: bool = False
    back_to_camera: bool = False
    camera_mode: str | None = None
    camera_explicit_current_request: bool = False
    framing_explicit_current_request: bool = False
    required_body_regions: list[str] = field(default_factory=list)
    forbidden_body_regions: list[str] = field(default_factory=list)
    realism_constraints: list[str] = field(default_factory=list)
    natural_capture_required: bool = True
    current_scene_from_chat: bool = False
    scene_context_summary: str | None = None
    identity_continuity_required: bool = True


@dataclass
class SemanticSourceReference:
    kind: str | None = None
    job_id: int | None = None
    message_id: int | None = None
    relative_reference: str | None = None


@dataclass
class SemanticImageDecision:
    action: str
    media_delivery_requested: bool
    confidence: float
    reason_code: str
    needs_clarification: bool = False
    source_reference: SemanticSourceReference | None = None
    visual_intent: VisualIntent = field(default_factory=VisualIntent)
    safety_relevant_signals: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in {a.value for a in SemanticImageAction}:
            raise ValueError(f"unsupported semantic image action: {self.action}")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.action == SemanticImageAction.CLARIFY and not self.needs_clarification:
            raise ValueError("clarify action must set needs_clarification")
        if self.action == SemanticImageAction.CHAT and self.media_delivery_requested:
            raise ValueError("chat cannot request media delivery")
        if isinstance(self.source_reference, dict):
            self.source_reference = SemanticSourceReference(**self.source_reference)
        if isinstance(self.visual_intent, dict):
            self.visual_intent = VisualIntent(**self.visual_intent)


def enforce_clear_image_request_action(
    deterministic_action: str | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Preserve extracted visuals while locking an unambiguous new-photo delivery command."""
    if deterministic_action != SemanticImageAction.GENERATE_NEW:
        return decision
    if decision.action in {SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}:
        return decision
    if (
        decision.action != SemanticImageAction.GENERATE_NEW
        or decision.needs_clarification
        or not decision.media_delivery_requested
    ):
        logger.info(
            "IMAGE_CLEAR_REQUEST_ACTION_LOCKED model_action=%s model_reason=%s",
            decision.action,
            decision.reason_code,
        )
    decision.action = SemanticImageAction.GENERATE_NEW
    decision.media_delivery_requested = True
    decision.needs_clarification = False
    decision.reason_code = "clear_image_delivery_action_locked"
    return decision


def enforce_new_photo_default(
    current_text: str,
    deterministic_action: str | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Default an image request to a new photo unless editing the previous image is explicit."""
    if decision.action != SemanticImageAction.CLARIFY:
        return decision
    if deterministic_action in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}:
        return decision
    normalized = _norm_intent_text(current_text)
    previous_markers = ("قبلی", "همون عکس", "همونو", "همین عکس", "این عکس")
    edit_markers = ("تغییر", "ویرایش", "ادیت", "عوض", "درست کن", "بهتر کن")
    explicitly_editing_previous = any(marker in normalized for marker in previous_markers) and any(marker in normalized for marker in edit_markers)
    if explicitly_editing_previous:
        return decision
    image_surface = any(marker in normalized for marker in ("عکس", "تصویر", "ببینمت", "نشونم بده", "نشانم بده", "بگیر تازه", "تازه ببینم"))
    if deterministic_action == SemanticImageAction.GENERATE_NEW or image_surface:
        logger.info("IMAGE_CLARIFICATION_DEFAULTED_TO_NEW model_reason=%s", decision.reason_code)
        decision.action = SemanticImageAction.GENERATE_NEW
        decision.media_delivery_requested = True
        decision.needs_clarification = False
        decision.source_reference = None
        decision.reason_code = "image_request_defaults_to_new_photo"
    return decision


def enforce_clarification_scope(
    current_text: str,
    pending_resolution: PendingImageClarificationResolution | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Never let stale image context turn ordinary conversation into a clarification loop."""
    if decision.action != SemanticImageAction.CLARIFY or pending_resolution is not None:
        return decision
    normalized = _norm_intent_text(current_text)
    explicit_visual_surface = any(
        marker in normalized for marker in ("عکس", "تصویر", "ببینمت", "نشونم بده", "نشانم بده")
    )
    if canonical_explicit_image_action(current_text) is not None or explicit_visual_surface:
        return decision
    logger.info(
        "IMAGE_CLARIFICATION_DOWNGRADED_TO_CHAT reason=no_current_image_request model_reason=%s",
        decision.reason_code,
    )
    return SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=max(float(decision.confidence), 0.8),
        reason_code="clarification_without_current_image_request",
        needs_clarification=False,
        source_reference=None,
        visual_intent=decision.visual_intent,
        safety_relevant_signals=decision.safety_relevant_signals,
    )


def _referenced_object_phrase(text: str) -> str | None:
    normalized = _norm_intent_text(text)
    words = normalized.split()
    if "از" not in words or not ({"اون", "همون", "این", "همین"} & set(words)):
        return None
    start = words.index("از") + 1
    stop_words = {"بده", "بدی", "بفرست", "بفرستی", "بساز", "بگیر", "بگیری", "ببینم", "ببین"}
    end = next((idx for idx in range(start, len(words)) if words[idx] in stop_words), len(words))
    candidate = [
        word for word in words[start:end]
        if word not in {"اون", "همون", "این", "همین", "عکس", "تصویر", "یه", "یک"}
    ]
    if not candidate:
        return None
    self_markers = {"خودت", "خودتو", "صورتت", "چهره", "بدنت", "لباست", "موهات"}
    if set(candidate) & self_markers:
        return None
    return " ".join(candidate)


def enforce_referenced_object_request(
    context: SemanticImageRouterContext,
    deterministic_action: str | None,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Resolve «that object from the previous photo» as a source-bound object detail photo."""
    if deterministic_action != SemanticImageAction.GENERATE_NEW:
        return decision
    phrase = _referenced_object_phrase(context.current_user_message)
    if not phrase:
        return decision
    visual = decision.visual_intent
    visual.request_type = "object_photo"
    visual.primary_subject = "object"
    visual.object_only = True
    visual.partner_visible = False
    visual.pet_only = False
    visual.hands_only = False
    visual.face_visible = False
    visual.face_hidden = True
    visual.camera_mode = "point_of_view"
    visual.framing = "detail"
    visual.visible_objects = list(dict.fromkeys([*(visual.visible_objects or []), phrase]))
    visual.natural_capture_required = True
    latest = context.recent_image_job or context.latest_image_job
    if latest and latest.has_retrievable_artifact and latest.job_id is not None:
        decision.action = SemanticImageAction.REFINE_PREVIOUS
        decision.source_reference = SemanticSourceReference(kind="latest_image", job_id=latest.job_id)
        decision.reason_code = "referenced_object_from_latest_image"
    else:
        decision.action = SemanticImageAction.GENERATE_NEW
        decision.source_reference = None
        decision.reason_code = "referenced_object_new_photo"
    decision.media_delivery_requested = True
    decision.needs_clarification = False
    logger.info(
        "IMAGE_REFERENCED_OBJECT_LOCKED action=%s source_job_id=%s object_phrase=%s",
        decision.action,
        getattr(decision.source_reference, "job_id", None),
        phrase,
    )
    return decision


def enforce_partner_photo_defaults(
    context: SemanticImageRouterContext,
    decision: SemanticImageDecision,
) -> SemanticImageDecision:
    """Apply product-level defaults for a real persistent partner photo.

    The semantic model remains authoritative for explicit camera, framing, scene,
    object, pet and body instructions. This only fills genuinely omitted fields.
    """
    if decision.action != SemanticImageAction.GENERATE_NEW or not decision.media_delivery_requested:
        return decision
    visual = decision.visual_intent
    primary = str(visual.primary_subject or "partner").strip().lower()
    if (
        primary not in {"partner", "person", "self"}
        or visual.partner_visible is False
        or visual.object_only
        or visual.pet_only
        or visual.hands_only
    ):
        return decision

    visual.primary_subject = "partner"
    visual.partner_visible = True
    visual.natural_capture_required = True
    visual.identity_continuity_required = True
    if not visual.camera_explicit_current_request:
        if visual.back_to_camera:
            visual.camera_mode = "tripod_timer"
        elif visual.framing == "full_body":
            visual.camera_mode = "mirror_selfie"
        else:
            visual.camera_mode = "casual_selfie"
    if not visual.framing_explicit_current_request and not visual.framing:
        visual.framing = "natural_medium_or_medium_wide"
    if visual.face_visible is None and not visual.face_hidden and not visual.back_to_camera:
        visual.face_visible = True

    contextual_scene_parts = [
        str(value).strip() for value in (
            visual.scene, visual.location, visual.environment_type, visual.activity,
            *(visual.required_visible_environment_elements or []),
        ) if value not in (None, "")
    ]
    semantic_scene_summary = str(visual.scene_context_summary or "").strip()
    semantic_scene_resolved = bool(visual.current_scene_from_chat and semantic_scene_summary)
    if not visual.scene_explicit_current_request:
        if semantic_scene_resolved:
            visual.current_scene_from_chat = True
            visual.scene_context_summary = semantic_scene_summary[:280]
        elif contextual_scene_parts:
            visual.current_scene_from_chat = True
            visual.scene_context_summary = "; ".join(dict.fromkeys(contextual_scene_parts))[:280]
        else:
            visual.current_scene_from_chat = False
            visual.scene_context_summary = None
        if visual.current_scene_from_chat and visual.scene_context_summary:
            scene_constraint = "Keep the photo in the partner's semantically resolved current location and activity from the conversation: " + visual.scene_context_summary
            if scene_constraint not in visual.freeform_visual_constraints:
                visual.freeform_visual_constraints.append(scene_constraint)

    for constraint in (
        "believable handheld phone capture",
        "same persistent partner identity as every previous photo",
        "not a staged third-person portrait unless explicitly requested",
    ):
        if constraint not in visual.realism_constraints:
            visual.realism_constraints.append(constraint)
    logger.info(
        "IMAGE_PARTNER_PHOTO_DEFAULTS_APPLIED action=%s camera_mode=%s framing=%s current_scene_from_chat=%s",
        decision.action, visual.camera_mode, visual.framing, visual.current_scene_from_chat,
    )
    return decision


@dataclass
class ConversationTurnSummary:
    role: str
    text_summary: str
    message_id: int | None = None
    created_at: str | None = None


@dataclass
class ReplyToMessageMetadata:
    message_id: int | None = None
    role: str | None = None
    media_kind: str | None = None
    text_summary: str | None = None


@dataclass
class RecentImageJobSummary:
    job_id: int | None = None
    status: str | None = None
    action: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    sent_at: str | None = None
    failed_at: str | None = None
    error_code: str | None = None
    request_chain_id: str | None = None
    has_retrievable_artifact: bool = False
    compact_user_visible_summary: str | None = None


@dataclass
class RecentResolvedImagePlanSummary:
    job_id: int | None = None
    action: str | None = None
    scene: str | None = None
    location: str | None = None
    environment_type: str | None = None
    privacy: str | None = None
    required_visible_environment_elements: list[str] = field(default_factory=list)
    scene_explicit_current_request: bool = False
    pose: str | None = None
    visible_fields: list[str] = field(default_factory=list)
    invariant_codes: list[str] = field(default_factory=list)


@dataclass
class SemanticImageRouterContext:
    current_user_message: str
    recent_conversation: list[ConversationTurnSummary] = field(default_factory=list)
    reply_to_message: ReplyToMessageMetadata | None = None
    active_image_job: RecentImageJobSummary | None = None
    latest_image_job: RecentImageJobSummary | None = None
    recent_image_job: RecentImageJobSummary | None = None
    recent_resolved_image_plan: RecentResolvedImagePlanSummary | None = None
    recent_retrievable_image_exists: bool = False
    seconds_since_recent_image: int | None = None
    legacy_route_decision: ImageRouteDecisionV2 | dict | None = None

    def redacted_payload(self, *, include_legacy: bool) -> dict[str, Any]:
        turns = self.recent_conversation[-10:]
        payload = {
            "schema_version": SEMANTIC_ROUTER_SCHEMA_VERSION,
            "current_user_message": self.current_user_message,
            "recent_conversation": [asdict(t) for t in turns],
            "reply_to_message": asdict(self.reply_to_message) if self.reply_to_message else None,
            "active_image_job": asdict(self.active_image_job) if self.active_image_job else None,
            "latest_image_job": asdict(self.latest_image_job) if self.latest_image_job else None,
            "recent_sent_image": asdict(self.recent_image_job) if self.recent_image_job else None,
            "recent_image_job_summary": asdict(self.recent_image_job) if self.recent_image_job else None,
            "recent_resolved_image_plan_summary": asdict(self.recent_resolved_image_plan) if self.recent_resolved_image_plan else None,
            "recent_retrievable_image_exists": self.recent_retrievable_image_exists,
            "seconds_since_recent_image": self.seconds_since_recent_image,
        }
        if include_legacy and self.legacy_route_decision is not None:
            payload["legacy_route_decision"] = asdict(self.legacy_route_decision) if hasattr(self.legacy_route_decision, "__dataclass_fields__") else dict(self.legacy_route_decision)
        return payload


class SemanticImageIntentModel(Protocol):
    async def classify(self, payload: dict[str, Any]) -> dict[str, Any]: ...


SEMANTIC_IMAGE_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "media_delivery_requested", "confidence", "reason_code", "needs_clarification", "source_reference", "visual_intent"],
    "properties": {
        "action": {"enum": [a.value for a in SemanticImageAction]},
        "media_delivery_requested": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_code": {"type": "string"},
        "needs_clarification": {"type": "boolean"},
        "source_reference": {"type": ["object", "null"]},
        "visual_intent": {"type": "object"},
        "safety_relevant_signals": {"type": "object"},
    },
}


@dataclass(frozen=True)
class SemanticRouterThresholds:
    generate_new: float = 0.82
    refine_previous: float = 0.84
    variation: float = 0.84
    resend_exact: float = 0.90
    chat: float = 0.70
    clarify_below: float = 0.74
    calibration_dataset: str = "app/evaluation/image_semantic_intent_dataset.json"


class SemanticImageIntentRouter:
    """Model-backed semantic router. Not wired to production execution in this PR."""

    def __init__(self, model: SemanticImageIntentModel, thresholds: SemanticRouterThresholds | None = None) -> None:
        self.model = model
        self.thresholds = thresholds or SemanticRouterThresholds()

    async def decide(self, context: SemanticImageRouterContext, *, shadow_or_evaluation: bool = True) -> SemanticImageDecision:
        payload = context.redacted_payload(include_legacy=shadow_or_evaluation)
        raw = await self.model.classify(payload)
        decision = SemanticImageDecision(**raw)
        framing=getattr(decision.visual_intent, "framing", None)
        if framing:
            logger.info("IMAGE_SEMANTIC_FRAMING_EXTRACTED user_id=%s job_id=%s request_chain_id=%s action=%s framing=%s reason_code=%s", getattr(context, "user_id", None), None, None, decision.action, framing, decision.reason_code)
        return self._calibrate(decision)

    def _calibrate(self, decision: SemanticImageDecision) -> SemanticImageDecision:
        if decision.needs_clarification or decision.action == SemanticImageAction.CLARIFY:
            return decision
        threshold = getattr(self.thresholds, str(decision.action), self.thresholds.clarify_below)
        if (decision.confidence < threshold and decision.action != SemanticImageAction.CHAT
                and not (decision.action == SemanticImageAction.GENERATE_NEW and decision.media_delivery_requested)):
            return SemanticImageDecision(
                action=SemanticImageAction.CLARIFY,
                media_delivery_requested=False,
                confidence=decision.confidence,
                reason_code="semantic_confidence_below_calibrated_threshold",
                needs_clarification=True,
                source_reference=decision.source_reference,
                visual_intent=decision.visual_intent,
                safety_relevant_signals=decision.safety_relevant_signals,
            )
        return decision


async def resolve_active_image_job_followup_semantically(
    context: SemanticImageRouterContext,
    decision: SemanticImageDecision,
    *,
    model=None,
) -> SemanticImageDecision:
    if decision.action not in {SemanticImageAction.CHAT, SemanticImageAction.CLARIFY}:
        return decision
    target=context.active_image_job
    if target is None:
        latest=context.latest_image_job
        latest_status=str(getattr(latest, 'status', '') or '') if latest else ''
        if latest_status == 'sent' and context.seconds_since_recent_image is not None and context.seconds_since_recent_image <= 600:
            target=latest
        elif latest_status in {'failed','delivery_failed'}:
            timestamp=getattr(latest, 'failed_at', None) or getattr(latest, 'created_at', None)
            try:
                age_seconds=max(0, int((datetime.utcnow()-datetime.fromisoformat(str(timestamp))).total_seconds()))
            except Exception:
                age_seconds=10**9
            if age_seconds <= 7200:
                target=latest
    if target is None:
        return decision
    if str(getattr(target, 'status', '') or '') not in {'queued','processing','generating','sending','delivery_failed','failed','sent'}:
        return decision
    semantic_model=model or VeniceSemanticImageIntentModel()
    payload={
        'current_user_message': context.current_user_message,
        'target_job': asdict(target),
        'recent_conversation': [asdict(turn) for turn in context.recent_conversation[-4:]],
    }
    system=(
        'An image job is relevant. Classify the current colloquial Persian follow-up as exactly one of status_query, cancel_pending, or chat. '
        'status_query includes any natural way of asking what happened, whether it is ready, where the photo is, or why it is taking long, even with typos, repeated letters, vocatives, names, jokes, or extra filler. '
        'cancel_pending means the user wants the image stopped. chat means neither. Return JSON only: {"action":"status_query|cancel_pending|chat","confidence":0.0}. Do not use phrase matching.'
    )
    try:
        result=await semantic_model.client.complete_result(
            [
                {'role':'system','content':system},
                {'role':'user','content':json.dumps(payload, ensure_ascii=False, sort_keys=True)},
            ],
            model=semantic_model.model,
            parameters={'temperature':0.0,'top_p':0.1,'max_tokens':80,'response_format':{'type':'json_object'}},
            timeout=min(float(getattr(semantic_model, 'timeout_seconds', 4.0)), 4.0),
        )
        data=json.loads(result.text or '{}')
        action=str(data.get('action') or 'chat')
        confidence=float(data.get('confidence') or 0.0)
    except Exception as exc:
        logger.info('IMAGE_ACTIVE_JOB_FOLLOWUP_MODEL_FAILED error=%s', type(exc).__name__)
        return decision
    if action not in {SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING} or confidence < 0.65:
        return decision
    logger.info('IMAGE_ACTIVE_JOB_FOLLOWUP_RESOLVED action=%s job_id=%s status=%s', action, target.job_id, target.status)
    return SemanticImageDecision(
        action=action,
        media_delivery_requested=False,
        confidence=confidence,
        reason_code='active_image_job_followup_semantic_control',
        needs_clarification=False,
        source_reference=None,
        visual_intent=decision.visual_intent,
        safety_relevant_signals=decision.safety_relevant_signals,
    )


def should_report_active_job_instead_of_enqueuing(context: SemanticImageRouterContext, decision: SemanticImageDecision) -> bool:
    return bool(context.active_image_job and decision.action == SemanticImageAction.GENERATE_NEW)

def semantic_shadow_log_event(context: SemanticImageRouterContext, decision: SemanticImageDecision, invariant_codes: list[str] | None = None) -> dict[str, Any]:
    legacy_action = None
    if context.legacy_route_decision is not None:
        legacy_action = getattr(context.legacy_route_decision, "action", None) or dict(context.legacy_route_decision).get("action")
    vi = asdict(decision.visual_intent)
    redacted_field_names={'identity_continuity_required','scene_context_summary'}
    extracted = sorted(k for k, v in vi.items() if k not in redacted_field_names and k != 'confidence' and v not in (None, False, "", [], {}) and not (k == 'natural_capture_required' and v is True))
    event = {
        "event": "IMAGE_SEMANTIC_ROUTE_SHADOW",
        "request_hash": hashlib.sha256((context.current_user_message or "").encode()).hexdigest()[:16],
        "source_message_id": getattr(context.reply_to_message, "message_id", None),
        "legacy_action": legacy_action,
        "semantic_action": decision.action,
        "media_delivery_requested": decision.media_delivery_requested,
        "confidence_bucket": _confidence_bucket(decision.confidence),
        "needs_clarification": decision.needs_clarification,
        "route_mismatch": bool(legacy_action and legacy_action != decision.action),
        "extracted_field_names": extracted,
        "invariant_codes": invariant_codes or [],
    }
    logger.info("IMAGE_SEMANTIC_ROUTE_SHADOW %s", json.dumps(event, ensure_ascii=False, sort_keys=True))
    return event


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 0.9: return "0.90-1.00"
    if confidence >= 0.8: return "0.80-0.89"
    if confidence >= 0.7: return "0.70-0.79"
    return "<0.70"


def validate_source_reference_deterministically(decision: SemanticImageDecision, *, recent_retrievable_image_exists: bool, allowed_job_ids: set[int]) -> tuple[bool, str | None]:
    if decision.action not in {SemanticImageAction.REFINE_PREVIOUS, SemanticImageAction.VARIATION, SemanticImageAction.RESEND_EXACT}:
        return True, None
    if not recent_retrievable_image_exists:
        return False, "no_recent_retrievable_image"
    ref = decision.source_reference
    if ref and ref.job_id is not None and ref.job_id not in allowed_job_ids:
        return False, "source_job_out_of_scope"
    return True, None

class VeniceSemanticImageIntentModel:
    """Venice-backed structured classifier for production semantic image routing."""
    def __init__(self, *, model: str = SEMANTIC_ROUTER_MODEL, timeout_seconds: float = 4.0) -> None:
        from app.llm.client import LLMClient
        self.client = LLMClient(); self.model=model; self.timeout_seconds=timeout_seconds

    async def classify(self, payload: dict[str, Any]) -> dict[str, Any]:
        system = (
            "Classify whether the user's current Persian message is chat or an image action. "
            "Actions: generate_new means a newly generated image; refine_previous changes a previous image; variation means another related image; resend_exact resends the exact prior artifact; status_query asks about an active/recent job; cancel_pending cancels it; chat discusses images without requesting delivery; clarify is only for genuine action/source ambiguity. "
            "Use current message, recent conversation, reply metadata, active/latest image job, and recent resolved plan. A direct answer to a prior clarification must resolve that clarification and must not create a loop. Short questions like چیشد or عکس کجاست are status_query when an image job is relevant. Confusion after an error is chat unless another image is explicitly requested. "
            "Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. For a generic request to see the partner now, default to a believable casual handheld selfie; use mirror_selfie for full-body unless the user explicitly requests timer/tripod or another camera method. "
            "Populate request_type and primary_subject as partner, pet, object, or scene. Set partner_visible, pet_visible, object_only, pet_only, hands_only, face_visible, face_hidden, and back_to_camera. Set camera_mode to casual_selfie, mirror_selfie, tripod_timer, point_of_view, passenger_pov, dashboard_mount, candid, or casual_phone_photo. Set camera_explicit_current_request=true only when the current user message explicitly requests the camera method; set framing_explicit_current_request=true only when the current user message explicitly requests framing. A full-body selfie normally means mirror_selfie unless timer/tripod is explicit. Coffee, food, personal-object, and pet photos may omit the partner. Hands-only means hands_only=true, face_hidden=true, hands in required_body_regions, and point_of_view unless another camera method is explicit. Back-view means back_to_camera=true. "
            "Extract scene/location/environment_type/privacy and mark scene_explicit_current_request=true when the current message names them. For requests meaning now/currently/from where you are, treat the most recent assistant statement about the partner current location, support surface and activity as authoritative current-world context. Always set current_scene_from_chat=true and provide a compact scene_context_summary when that statement contains current-world information, even when you cannot confidently canonicalize every scene/location/activity field. Do not silently replace that current scene with a routine or generic home/street default. Extract pose, activity, wardrobe, framing, gaze, visible_objects, held_objects, required and forbidden body regions, and freeform constraints. Preserve explicit current instructions over conversation context, and conversation context over routine context. A private location alone is not adult intent. "
            "Set natural_capture_required=true unless studio/editorial imagery is explicitly requested. The result must behave like a plausible personal photo from a real partner: avoid ID/passport/casting defaults and impossible self-photography while driving. "
            "For adult visual requests set nudity_level to normal, suggestive, lingerie, topless, or full_nudity. Explicit genital/anatomy focus sets explicit_anatomy_focus=true, includes genitals in body_or_face_regions, and sets safety_relevant_signals.explicit_genital_visibility=true. Adult image access is checked elsewhere; do not add a confirmation flow here. "
            "Return only valid JSON matching the schema. Do not decide billing, entitlement, source ownership, provider execution, or delivery."
        )
        user_payload={"schema": SEMANTIC_IMAGE_DECISION_JSON_SCHEMA, "context": payload}
        params={"temperature":0.05,"top_p":0.1,"max_tokens":700,"response_format":{"type":"json_object"}}
        last_error='invalid_json'
        for attempt in range(2):
            result = await self.client.complete_result([
                {"role":"system","content":system},
                {"role":"user","content":json.dumps(user_payload, ensure_ascii=False, sort_keys=True)},
            ], model=self.model, parameters=params, timeout=self.timeout_seconds)
            try:
                if result.error and not result.text:
                    raise ValueError('model_error')
                data=json.loads(result.text)
                SemanticImageDecision(**data)
                return data
            except Exception as exc:
                last_error=type(exc).__name__
                if attempt == 0:
                    continue
        logger.info('IMAGE_SEMANTIC_MODEL_FAILED error=%s', last_error)
        return {"action": SemanticImageAction.CLARIFY, "media_delivery_requested": False, "confidence": 0.0, "reason_code": "semantic_model_failure", "needs_clarification": True, "source_reference": None, "visual_intent": {}, "safety_relevant_signals": {}}
