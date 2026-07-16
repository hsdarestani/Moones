from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.services.image_pipeline_v2 import ImageRouteDecisionV2

logger = logging.getLogger(__name__)

SEMANTIC_ROUTER_MODEL_PROVIDER = "venice"
SEMANTIC_ROUTER_MODEL = "qwen-3-6-plus"
SEMANTIC_ROUTER_SCHEMA_VERSION = "semantic-image-intent-v1"
SEMANTIC_ROUTER_ESTIMATED_LATENCY_MS = 900
SEMANTIC_ROUTER_ESTIMATED_COST_USD = 0.0007


class SemanticImageAction(StrEnum):
    CHAT = "chat"
    GENERATE_NEW = "generate_new"
    REFINE_PREVIOUS = "refine_previous"
    VARIATION = "variation"
    RESEND_EXACT = "resend_exact"
    CLARIFY = "clarify"


@dataclass
class VisualIntent:
    subject_focus: str | None = None
    body_or_face_regions: list[str] = field(default_factory=list)
    scene: str | None = None
    location: str | None = None
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
    freeform_visual_constraints: list[str] = field(default_factory=list)


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
    sent_at: str | None = None
    has_retrievable_artifact: bool = False
    compact_user_visible_summary: str | None = None


@dataclass
class RecentResolvedImagePlanSummary:
    job_id: int | None = None
    action: str | None = None
    scene: str | None = None
    location: str | None = None
    pose: str | None = None
    visible_fields: list[str] = field(default_factory=list)
    invariant_codes: list[str] = field(default_factory=list)


@dataclass
class SemanticImageRouterContext:
    current_user_message: str
    recent_conversation: list[ConversationTurnSummary] = field(default_factory=list)
    reply_to_message: ReplyToMessageMetadata | None = None
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
    "required": [
        "action",
        "media_delivery_requested",
        "confidence",
        "reason_code",
        "needs_clarification",
        "source_reference",
        "visual_intent",
        "safety_relevant_signals",
    ],
    "properties": {
        "action": {
            "enum": [
                action.value
                for action in SemanticImageAction
            ],
        },
        "media_delivery_requested": {
            "type": "boolean",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "reason_code": {
            "type": "string",
        },
        "needs_clarification": {
            "type": "boolean",
        },
        "source_reference": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "kind": {
                    "type": ["string", "null"],
                },
                "job_id": {
                    "type": ["integer", "null"],
                },
                "message_id": {
                    "type": ["integer", "null"],
                },
                "relative_reference": {
                    "type": ["string", "null"],
                },
            },
        },
        "visual_intent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "subject_focus": {
                    "type": ["string", "null"],
                },
                "body_or_face_regions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "scene": {
                    "type": ["string", "null"],
                },
                "location": {
                    "type": ["string", "null"],
                },
                "pose": {
                    "type": ["string", "null"],
                },
                "activity": {
                    "type": ["string", "null"],
                },
                "expression": {
                    "type": ["string", "null"],
                },
                "wardrobe": {
                    "type": ["string", "null"],
                },
                "visible_objects": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "held_objects": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "camera": {
                    "type": ["string", "null"],
                },
                "framing": {
                    "type": ["string", "null"],
                },
                "lighting": {
                    "type": ["string", "null"],
                },
                "exclusions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "freeform_visual_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "safety_relevant_signals": {
            "type": "object",
        },
    },
}


_SEMANTIC_ACTION_ALIASES = {
    "chat": "chat",
    "conversation": "chat",
    "talk": "chat",
    "discuss": "chat",

    "generate_new": "generate_new",
    "generate": "generate_new",
    "new_image": "generate_new",
    "create_image": "generate_new",
    "send_image": "generate_new",
    "image": "generate_new",

    "refine_previous": "refine_previous",
    "refine": "refine_previous",
    "edit_previous": "refine_previous",
    "modify_previous": "refine_previous",

    "variation": "variation",
    "another": "variation",
    "another_one": "variation",
    "new_variation": "variation",

    "resend_exact": "resend_exact",
    "resend": "resend_exact",
    "send_again": "resend_exact",
    "same_again": "resend_exact",

    "clarify": "clarify",
    "ask_clarification": "clarify",
    "ambiguous": "clarify",
}


_VISUAL_STRING_FIELDS = {
    "subject_focus",
    "scene",
    "location",
    "pose",
    "activity",
    "expression",
    "wardrobe",
    "camera",
    "framing",
    "lighting",
}


_VISUAL_LIST_FIELDS = {
    "body_or_face_regions",
    "visible_objects",
    "held_objects",
    "exclusions",
    "freeform_visual_constraints",
}


_VISUAL_FIELD_ALIASES = {
    "subject": "subject_focus",
    "focus": "subject_focus",

    "body_regions": "body_or_face_regions",
    "face_regions": "body_or_face_regions",
    "body_parts": "body_or_face_regions",

    "place": "location",
    "environment": "scene",

    "clothing": "wardrobe",
    "clothes": "wardrobe",

    "objects": "visible_objects",
    "visible_items": "visible_objects",
    "items": "visible_objects",

    "held_object": "held_objects",
    "object_in_hand": "held_objects",

    "shot": "framing",
    "shot_type": "framing",
    "camera_mode": "camera",

    "negative_constraints": "exclusions",

    "constraints": "freeform_visual_constraints",
    "details": "freeform_visual_constraints",
    "freeform": "freeform_visual_constraints",
}


def _semantic_string_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []

    if isinstance(value, (list, tuple, set)):
        output: list[str] = []

        for item in value:
            if item is None:
                continue

            text = str(item).strip()

            if text:
                output.append(text)

        return output

    text = str(value).strip()

    return [text] if text else []


def _extract_semantic_json(
    raw_text: str,
) -> dict[str, Any]:
    value = str(raw_text or "").strip()

    if value.startswith("```"):
        lines = value.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        value = "\n".join(lines).strip()

    try:
        parsed = json.loads(value)
    except Exception:
        start = value.find("{")
        end = value.rfind("}")

        if start < 0 or end <= start:
            raise ValueError(
                "semantic_json_object_missing"
            )

        parsed = json.loads(
            value[start:end + 1]
        )

    if not isinstance(parsed, dict):
        raise ValueError(
            "semantic_top_level_not_object"
        )

    return parsed


def _normalize_semantic_payload(
    data: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(
            "semantic_payload_not_object"
        )

    raw_action = str(
        data.get("action") or ""
    ).strip().lower()

    action = _SEMANTIC_ACTION_ALIASES.get(
        raw_action,
        raw_action,
    )

    allowed_actions = {
        item.value
        for item in SemanticImageAction
    }

    if action not in allowed_actions:
        raise ValueError(
            "semantic_action_invalid"
        )

    requested_clarification = bool(
        data.get("needs_clarification", False)
    )

    if requested_clarification:
        action = "clarify"

    try:
        confidence = float(
            data.get("confidence", 0.0)
        )
    except Exception:
        confidence = 0.0

    confidence = max(
        0.0,
        min(1.0, confidence),
    )

    reason_code = str(
        data.get("reason_code")
        or "semantic_model_decision"
    ).strip()

    source_raw = data.get(
        "source_reference"
    )

    source_reference = None

    if isinstance(source_raw, dict):
        job_id = source_raw.get(
            "job_id",
            source_raw.get("source_job_id"),
        )

        message_id = source_raw.get(
            "message_id",
            source_raw.get(
                "source_message_id"
            ),
        )

        try:
            job_id = (
                int(job_id)
                if job_id is not None
                else None
            )
        except Exception:
            job_id = None

        try:
            message_id = (
                int(message_id)
                if message_id is not None
                else None
            )
        except Exception:
            message_id = None

        source_reference = {
            "kind": (
                str(source_raw.get("kind"))
                if source_raw.get("kind")
                is not None
                else (
                    "recent_image"
                    if job_id is not None
                    else None
                )
            ),
            "job_id": job_id,
            "message_id": message_id,
            "relative_reference": (
                str(
                    source_raw.get(
                        "relative_reference"
                    )
                )
                if source_raw.get(
                    "relative_reference"
                )
                is not None
                else None
            ),
        }

    elif source_raw not in (None, ""):
        source_reference = {
            "kind": None,
            "job_id": None,
            "message_id": None,
            "relative_reference": str(
                source_raw
            ),
        }

    visual_raw = data.get(
        "visual_intent"
    ) or {}

    if not isinstance(visual_raw, dict):
        visual_raw = {
            "freeform_visual_constraints":
                _semantic_string_list(
                    visual_raw
                ),
        }

    visual: dict[str, Any] = {
        key: None
        for key in _VISUAL_STRING_FIELDS
    }

    visual.update({
        key: []
        for key in _VISUAL_LIST_FIELDS
    })

    unknown_constraints: list[str] = []

    for raw_key, raw_value in visual_raw.items():
        key = _VISUAL_FIELD_ALIASES.get(
            str(raw_key),
            str(raw_key),
        )

        if key in _VISUAL_STRING_FIELDS:
            if raw_value is None:
                visual[key] = None
            else:
                value = str(
                    raw_value
                ).strip()

                visual[key] = (
                    value or None
                )

            continue

        if key in _VISUAL_LIST_FIELDS:
            visual[key] = (
                _semantic_string_list(
                    raw_value
                )
            )
            continue

        if raw_value not in (
            None,
            "",
            [],
            {},
        ):
            if isinstance(
                raw_value,
                (dict, list),
            ):
                serialized = json.dumps(
                    raw_value,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            else:
                serialized = str(
                    raw_value
                )

            unknown_constraints.append(
                f"{raw_key}: {serialized}"
            )

    visual[
        "freeform_visual_constraints"
    ] = list(dict.fromkeys(
        visual[
            "freeform_visual_constraints"
        ]
        + unknown_constraints
    ))

    for key in _VISUAL_LIST_FIELDS:
        visual[key] = list(
            dict.fromkeys(
                visual.get(key) or []
            )
        )

    safety = data.get(
        "safety_relevant_signals"
    )

    if not isinstance(safety, dict):
        safety = {}

    media_delivery_requested = (
        action
        not in {
            "chat",
            "clarify",
        }
    )

    needs_clarification = (
        action == "clarify"
    )

    return {
        "action": action,
        "media_delivery_requested":
            media_delivery_requested,
        "confidence": confidence,
        "reason_code": reason_code,
        "needs_clarification":
            needs_clarification,
        "source_reference":
            source_reference,
        "visual_intent": visual,
        "safety_relevant_signals":
            safety,
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
        return self._calibrate(decision)

    def _calibrate(self, decision: SemanticImageDecision) -> SemanticImageDecision:
        if decision.needs_clarification or decision.action == SemanticImageAction.CLARIFY:
            return decision
        threshold = getattr(self.thresholds, str(decision.action), self.thresholds.clarify_below)
        if decision.confidence < threshold and decision.action != SemanticImageAction.CHAT:
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
    extracted = sorted(k for k, v in vi.items() if v not in (None, "", [], {}))
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
    """Venice-backed semantic image classifier."""

    def __init__(
        self,
        *,
        model: str = SEMANTIC_ROUTER_MODEL,
        timeout_seconds: float = 8.0,
    ) -> None:
        from app.llm.client import LLMClient

        self.client = LLMClient()
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def classify(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        system = """
You classify the communicative intent of Persian
messages sent to a fictional relationship companion.

Understand meaning and conversation context.
Do not classify by keyword presence.

The user may request an image without using words like:
عکس، تصویر، بفرست، نشون بده.

Allowed actions:

chat:
The user is talking, asking, criticizing, or discussing
an image without asking for media delivery.

generate_new:
The user wants to see the companion or receive a new
image.

refine_previous:
The user wants the latest valid image changed.

variation:
The user wants another version based on the previous
image.

resend_exact:
The user wants the exact previous image sent again.

clarify:
The request is genuinely ambiguous.

Persian examples:

دلم میخواد ببینمت
=> generate_new

بذار ببینمت
=> generate_new

الان چه شکلی هستی نشونم بده
=> generate_new

بازوهاتو ببینم
=> generate_new

بازوهات درد می‌کنه؟
=> chat

عکس قبلی چرا مصنوعی بود؟
=> chat

عکس قبلی مصنوعی بود، درستش کن
=> refine_previous

یکی دیگه شبیه قبلی
=> variation

همونو دوباره بفرست
=> resend_exact

یه چیزی از خودت بفرست
=> generate_new when visual intent is clear from
context; otherwise clarify.

Extract visual instructions into visual_intent.

Arbitrary objects are allowed.

Unknown visual details must be placed inside:
freeform_visual_constraints

Never invent JSON keys.

Return exactly one JSON object.
No markdown.
No explanation.
No reasoning.
No code fences.

Do not authorize billing, policy, source ownership,
or provider execution.
""".strip()

        output_contract = {
            "action": (
                "chat | generate_new | "
                "refine_previous | variation | "
                "resend_exact | clarify"
            ),
            "media_delivery_requested":
                "boolean",
            "confidence": "number 0..1",
            "reason_code": "short_string",
            "needs_clarification":
                "boolean",
            "source_reference": (
                None
            ),
            "visual_intent": {
                "subject_focus": None,
                "body_or_face_regions": [],
                "scene": None,
                "location": None,
                "pose": None,
                "activity": None,
                "expression": None,
                "wardrobe": None,
                "visible_objects": [],
                "held_objects": [],
                "camera": None,
                "framing": None,
                "lighting": None,
                "exclusions": [],
                "freeform_visual_constraints":
                    [],
            },
            "safety_relevant_signals": {},
        }

        examples = [
            (
                "دلم میخواد ببینمت",
                {
                    "action": "generate_new",
                    "media_delivery_requested":
                        True,
                    "confidence": 0.97,
                    "reason_code":
                        "implicit_request_to_see_companion",
                    "needs_clarification":
                        False,
                    "source_reference": None,
                    "visual_intent": {},
                    "safety_relevant_signals":
                        {},
                },
            ),
            (
                "عکس قبلی چرا مصنوعی بود؟",
                {
                    "action": "chat",
                    "media_delivery_requested":
                        False,
                    "confidence": 0.97,
                    "reason_code":
                        "discussion_of_previous_image",
                    "needs_clarification":
                        False,
                    "source_reference": None,
                    "visual_intent": {},
                    "safety_relevant_signals":
                        {},
                },
            ),
            (
                (
                    "یه عکس بده توی کافه باشی "
                    "و لیوان قهوه دستت باشه"
                ),
                {
                    "action": "generate_new",
                    "media_delivery_requested":
                        True,
                    "confidence": 0.99,
                    "reason_code":
                        "explicit_new_image_request",
                    "needs_clarification":
                        False,
                    "source_reference": None,
                    "visual_intent": {
                        "scene": "cafe",
                        "held_objects": [
                            "coffee cup",
                        ],
                    },
                    "safety_relevant_signals":
                        {},
                },
            ),
            (
                "یکی دیگه شبیه قبلی",
                {
                    "action": "variation",
                    "media_delivery_requested":
                        True,
                    "confidence": 0.96,
                    "reason_code":
                        "request_for_variation",
                    "needs_clarification":
                        False,
                    "source_reference": {
                        "kind": "recent_image",
                        "job_id": None,
                        "message_id": None,
                        "relative_reference":
                            "previous image",
                    },
                    "visual_intent": {},
                    "safety_relevant_signals":
                        {},
                },
            ),
        ]

        messages = [
            {
                "role": "system",
                "content": system,
            },
        ]

        for example_input, example_output in examples:
            messages.append({
                "role": "user",
                "content": example_input,
            })

            messages.append({
                "role": "assistant",
                "content": json.dumps(
                    example_output,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            })

        messages.append({
            "role": "user",
            "content": json.dumps(
                {
                    "output_contract":
                        output_contract,
                    "context": payload,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        })

        parameters = {
            "temperature": 0.05,
            "top_p": 0.1,
            "max_tokens": 700,
            "response_format": {
                "type": "json_object",
            },
        }

        last_error = "unknown"

        for attempt in range(2):
            current_messages = list(
                messages
            )

            if attempt == 1:
                current_messages.append({
                    "role": "system",
                    "content": (
                        "Your previous answer was "
                        "invalid. Return exactly one "
                        "JSON object using only the "
                        "specified keys. Put unknown "
                        "visual details inside "
                        "freeform_visual_constraints."
                    ),
                })

            result = await self.client.complete_result(
                current_messages,
                model=self.model,
                parameters=parameters,
                timeout=self.timeout_seconds,
            )

            try:
                if result.error and not result.text:
                    raise ValueError(
                        "semantic_provider_error"
                    )

                raw_data = _extract_semantic_json(
                    result.text
                )

                normalized = (
                    _normalize_semantic_payload(
                        raw_data
                    )
                )

                SemanticImageDecision(
                    **normalized
                )

                return normalized

            except Exception as exc:
                last_error = type(exc).__name__

                logger.info(
                    "IMAGE_SEMANTIC_MODEL_ATTEMPT_FAILED "
                    "status_code=%s "
                    "error_type=%s "
                    "extraction_path=%s "
                    "response_length=%s "
                    "attempt=%s",
                    result.status_code,
                    last_error,
                    result.extraction_path,
                    len(result.text or ""),
                    attempt + 1,
                )

        logger.info(
            "IMAGE_SEMANTIC_MODEL_FAILED "
            "error_type=%s",
            last_error,
        )

        return {
            "action":
                SemanticImageAction.CLARIFY,
            "media_delivery_requested":
                False,
            "confidence": 0.0,
            "reason_code":
                "semantic_model_failure",
            "needs_clarification":
                True,
            "source_reference": None,
            "visual_intent": {},
            "safety_relevant_signals": {},
        }
