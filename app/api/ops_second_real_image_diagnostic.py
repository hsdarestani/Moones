from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.image_generation import ImageGenerationArtifact, ImageGenerationJob
from app.models.message import Message


router = APIRouter()
_TOKEN = "moones-second-real-image-20260731-9c7e41b2a0"


def _hash_text(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()[:20]


def _resolved_value(plan: dict, field: str):
    value = plan.get(field)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _safe_scalar(value: Any, max_len: int = 420):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_len]
    if isinstance(value, list):
        return [_safe_scalar(v, max_len=max_len) for v in value[:40]]
    if isinstance(value, dict):
        return {str(k): _safe_scalar(v, max_len=max_len) for k, v in list(value.items())[:60]}
    return str(value)[:max_len]


def _safe_selected_metadata(meta: dict) -> dict:
    explicit = {
        "route_action",
        "content_classification",
        "adult_intent",
        "selected_generation_model",
        "final_generation_model",
        "provider_model_attempts",
        "final_qa_reason_codes",
        "qa_reason_codes",
        "qa_failure_codes",
        "last_qa_reason_codes",
        "qa_degraded_provider_unavailable",
        "generation_failure_reason_codes",
        "failure_reason_codes",
        "fulfillment_failure_codes",
        "invariant_codes",
        "plan_invariant_codes",
        "prompt_invariant_codes",
        "continuity_source_job_id",
        "source_image_job_id",
        "current_image_state",
        "request_chain_id",
        "expected_subject_count",
        "provider_retry_count",
        "provider_attempt_count",
        "generation_attempt_count",
    }
    blocked_fragments = (
        "prompt",
        "user_request",
        "raw_text",
        "message_text",
        "image_bytes",
        "base64",
        "api_key",
        "token",
        "secret",
        "identity_descriptor",
        "identity_anchor",
    )
    out = {}
    for key, value in meta.items():
        k = str(key)
        kl = k.lower()
        if any(fragment in kl for fragment in blocked_fragments):
            continue
        if k in explicit or any(term in kl for term in ("qa_", "reason_code", "invariant", "attempt", "provider_model", "scene_guard", "identity_guard", "failure_code")):
            out[k] = _safe_scalar(value)
    return out


def _safe_job(db, job: ImageGenerationJob) -> dict:
    meta = dict(job.metadata_json or {})
    plan = dict(job.resolved_plan_json or meta.get("resolved_plan") or {})
    requirements = dict(meta.get("visual_requirements") or plan.get("visual_requirements") or {})
    must = dict(requirements.get("must_satisfy") or {})
    contract = dict(requirements.get("photo_contract") or {})
    artifact = db.scalar(
        select(ImageGenerationArtifact)
        .where(ImageGenerationArtifact.job_id == job.id)
        .limit(1)
    )
    safe_must = {
        key: _safe_scalar(value)
        for key, value in must.items()
        if key in {
            "required_scene_elements",
            "required_pose_elements",
            "required_wardrobe_elements",
            "required_support_surface_elements",
            "required_visible_objects",
            "required_body_regions",
            "forbidden_body_regions",
            "camera_mode",
            "partner_visible",
            "natural_capture_required",
            "framing",
            "full_body_visible",
            "head_visible",
            "feet_visible",
            "body_not_cropped",
            "closeup_forbidden",
            "tight_portrait_forbidden",
        }
    }
    return {
        "id": job.id,
        "created_at": str(job.created_at),
        "updated_at": str(job.updated_at),
        "started_at": str(job.started_at),
        "generated_at": str(job.generated_at),
        "sent_at": str(job.sent_at),
        "failed_at": str(job.failed_at),
        "status": job.status,
        "image_action": job.image_action,
        "source_image_job_id": job.source_image_job_id,
        "request_chain_id": job.request_chain_id,
        "request_hash": _hash_text(job.user_request),
        "correlation_hash": _hash_text(job.correlation_id),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "model": job.model,
        "provider": job.provider,
        "error_code": job.error_code,
        "error_message": str(job.error_message or "")[:500],
        "identity_fingerprint_prefix": str(job.identity_fingerprint or "")[:12] or None,
        "scene": _resolved_value(plan, "scene"),
        "location": _resolved_value(plan, "location"),
        "activity": _resolved_value(plan, "activity"),
        "pose": _resolved_value(plan, "pose"),
        "camera": _resolved_value(plan, "camera"),
        "support_surface": _resolved_value(plan, "support_surface"),
        "wardrobe": _resolved_value(plan, "wardrobe"),
        "required_objects": _resolved_value(plan, "required_objects"),
        "excluded_objects": _resolved_value(plan, "excluded_objects"),
        "plan_validation_results": _safe_scalar(plan.get("validation_results")),
        "framing": requirements.get("framing_requirement") or (plan.get("composition") or {}).get("framing"),
        "visual_reason_codes": _safe_scalar(requirements.get("reason_codes")),
        "must_satisfy": safe_must,
        "photo_contract_flags": {
            key: _safe_scalar(contract.get(key))
            for key in (
                "current_scene_from_chat",
                "camera_mode",
                "camera_explicit_current_request",
                "partner_visible",
                "identity_consistency_required",
                "expected_human_subject_count",
            )
            if key in contract
        },
        "metadata_keys": sorted(str(k) for k in meta.keys()),
        "selected_metadata": _safe_selected_metadata(meta),
        "artifact_present": bool(artifact and artifact.image_bytes),
        "artifact_byte_size": getattr(artifact, "byte_size", None),
    }


def _safe_message(message: Message) -> dict:
    meta = dict(message.metadata_json or {})
    safe_meta = {}
    for key, value in meta.items():
        kl = str(key).lower()
        if any(fragment in kl for fragment in ("prompt", "raw", "content", "text", "token", "secret")):
            continue
        if any(term in kl for term in ("image", "status", "billing", "error", "reason", "invariant", "route", "action", "job", "source", "kind")):
            safe_meta[str(key)] = _safe_scalar(value)
    return {
        "id": message.id,
        "user_id": message.user_id,
        "role": message.role,
        "created_at": str(message.created_at),
        "telegram_message_id": message.telegram_message_id,
        "content_hash": _hash_text(message.content),
        "metadata_keys": sorted(str(k) for k in meta.keys()),
        "selected_metadata": safe_meta,
    }


@router.get("/ops/second-real-image-9c7e41b2a0")
def second_real_image_diagnostic(x_ops_token: str | None = Header(default=None)) -> dict:
    if x_ops_token != _TOKEN:
        raise HTTPException(status_code=404, detail="not found")
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=2)
        jobs = db.scalars(
            select(ImageGenerationJob)
            .where(ImageGenerationJob.created_at >= cutoff)
            .order_by(ImageGenerationJob.id.desc())
            .limit(40)
        ).all()
        user_ids = sorted({job.user_id for job in jobs})
        messages = []
        if user_ids:
            messages = db.scalars(
                select(Message)
                .where(Message.user_id.in_(user_ids), Message.created_at >= cutoff)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(160)
            ).all()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "target_request_hashes": [
                "02b3527e704982a1ee65",
                "6d44ca8022b99674abc8",
            ],
            "jobs": [_safe_job(db, job) for job in jobs],
            "messages": [_safe_message(message) for message in messages],
        }
    finally:
        db.close()
