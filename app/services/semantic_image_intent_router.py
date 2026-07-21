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
    normalized = " ".join(normalized.split())
    collapsed: list[str] = []
    for char in normalized:
        if collapsed and char == collapsed[-1] and char != " ":
            continue
        collapsed.append(char)
    return "".join(collapsed)


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
    """Resolve only an active, recent clarification and leave unclear replies untouched."""
    normalized = normalize_image_clarification_text(text)
    action = next((key for key, answers in _NORMALIZED_CLARIFICATION_ANSWERS.items() if normalized in answers), None)
    if action is None:
        return None
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
    required_body_regions: list[str] = field(default_factory=list)
    forbidden_body_regions: list[str] = field(default_factory=list)
    realism_constraints: list[str] = field(default_factory=list)
    natural_capture_required: bool = True


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
    """Preserve LLM visual extraction but lock an unambiguous new-photo delivery action."""
    if deterministic_action != SemanticImageAction.GENERATE_NEW:
        return decision
    if decision.action in {SemanticImageAction.STATUS_QUERY, SemanticImageAction.CANCEL_PENDING}:
        return decision
    if decision.action != SemanticImageAction.GENERATE_NEW or decision.needs_clarification or not decision.media_delivery_requested:
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


def semantic_shadow_log_event(context: SemanticImageRouterContext, decision: SemanticImageDecision, invariant_codes: list[str] | None = None) -> dict[str, Any]:
    legacy_action = None
    if context.legacy_route_decision is not None:
        legacy_action = getattr(context.legacy_route_decision, "action", None) or dict(context.legacy_route_decision).get("action")
    vi = asdict(decision.visual_intent)
    extracted = sorted(k for k, v in vi.items() if k != 'confidence' and v not in (None, False, "", [], {}) and not (k == 'natural_capture_required' and v is True))
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
            "Never choose clarify for a straightforward photo request: ordinary, flirty, lingerie, nude, explicit adult, pet, object, hands-only, face-hidden, back-view, selfie, mirror selfie, timer/tripod, driving, cafe, bedroom, bathroom, nature, city, or car. Choose generate_new and produce the most complete structured visual intent. "
            "Populate request_type and primary_subject as partner, pet, object, or scene. Set partner_visible, pet_visible, object_only, pet_only, hands_only, face_visible, face_hidden, and back_to_camera. Set camera_mode to casual_selfie, mirror_selfie, tripod_timer, point_of_view, passenger_pov, dashboard_mount, candid, or casual_phone_photo. A full-body selfie normally means mirror_selfie unless timer/tripod is explicit. Coffee, food, personal-object, and pet photos may omit the partner. Hands-only means hands_only=true, face_hidden=true, hands in required_body_regions, and point_of_view unless another camera method is explicit. Back-view means back_to_camera=true. "
            "Extract scene/location/environment_type/privacy and mark scene_explicit_current_request=true when the current message names them. Extract pose, activity, wardrobe, framing, gaze, visible_objects, held_objects, required and forbidden body regions, and freeform constraints. Preserve explicit current instructions over routine context. A private location alone is not adult intent. "
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
