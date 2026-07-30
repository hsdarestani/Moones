from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.image_generation import ImageGenerationArtifact, ImageGenerationJob
from app.models.message import Message

router = APIRouter()
_TOKEN = "moones-imgaccept-20260730-b71e4c9fd2a658"


def _hash_text(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()[:20]


def _resolved_value(plan: dict, field: str):
    value = plan.get(field)
    if isinstance(value, dict):
        return value.get("value")
    return value


def _safe_job(db, job: ImageGenerationJob) -> dict:
    meta = dict(job.metadata_json or {})
    plan = dict(job.resolved_plan_json or meta.get("resolved_plan") or {})
    requirements = dict(meta.get("visual_requirements") or plan.get("visual_requirements") or {})
    contract = dict(requirements.get("photo_contract") or {})
    must = dict(requirements.get("must_satisfy") or {})
    artifact = db.scalar(
        select(ImageGenerationArtifact)
        .where(ImageGenerationArtifact.job_id == job.id)
        .limit(1)
    )
    artifact_present = bool(artifact and artifact.image_bytes)
    now = datetime.utcnow()
    age_seconds = None
    if job.sent_at:
        age_seconds = max(0, int((now - job.sent_at).total_seconds()))
    return {
        "id": job.id,
        "user_id": job.user_id,
        "chat_id_hash": _hash_text(str(job.chat_id)),
        "status": job.status,
        "created_at": str(job.created_at),
        "sent_at": str(job.sent_at),
        "failed_at": str(job.failed_at),
        "sent_age_seconds": age_seconds,
        "image_action": str(job.image_action),
        "source_image_job_id": job.source_image_job_id,
        "request_chain_id": job.request_chain_id,
        "user_request_hash": _hash_text(job.user_request),
        "content_mode": job.content_mode,
        "content_classification": meta.get("content_classification"),
        "adult_intent": meta.get("adult_intent"),
        "adult_private_scene_policy_applied": meta.get("adult_private_scene_policy_applied"),
        "model": job.model,
        "selected_generation_model": meta.get("selected_generation_model"),
        "final_generation_model": meta.get("final_generation_model"),
        "scene": _resolved_value(plan, "scene"),
        "location": _resolved_value(plan, "location"),
        "activity": _resolved_value(plan, "activity"),
        "pose": _resolved_value(plan, "pose"),
        "camera": _resolved_value(plan, "camera"),
        "framing": requirements.get("framing_requirement") or (plan.get("composition") or {}).get("framing"),
        "current_scene_from_chat": contract.get("current_scene_from_chat"),
        "scene_context_summary_hash": _hash_text(contract.get("scene_context_summary")) if contract.get("scene_context_summary") else None,
        "required_scene_elements": must.get("required_scene_elements"),
        "explicit_nudity_requested": requirements.get("explicit_nudity_requested"),
        "anatomy_qa_required": requirements.get("anatomy_qa_required"),
        "anatomical_profile": requirements.get("anatomical_profile"),
        "body_visibility_keys": sorted((meta.get("body_visibility") or {}).keys()),
        "provider_model_attempts": meta.get("provider_model_attempts"),
        "final_qa_reason_codes": meta.get("final_qa_reason_codes"),
        "qa_degraded_provider_unavailable": meta.get("qa_degraded_provider_unavailable"),
        "error_code": job.error_code,
        "error_message": str(job.error_message or "")[:220],
        "artifact_present": artifact_present,
        "artifact_byte_size": getattr(artifact, "byte_size", None),
        "artifact_cleared_at": str(getattr(artifact, "cleared_at", None)),
        "retrievable_under_30m_rule": bool(artifact_present and job.status == "sent" and job.sent_at and job.sent_at >= now - timedelta(minutes=30)),
        "retrievable_under_24h_rule": bool(artifact_present and job.status == "sent" and job.sent_at and job.sent_at >= now - timedelta(hours=24)),
    }


def _safe_message(message: Message) -> dict:
    meta = dict(message.metadata_json or {})
    safe_meta_keys = [
        key for key in (
            "source", "kind", "status", "billing_status", "user_move_intent",
            "natural_style_guard_rewrite", "natural_style_guard_fallback",
            "style_meta_talk_guard_applied", "disable_human_extras",
        ) if key in meta
    ]
    return {
        "id": message.id,
        "user_id": message.user_id,
        "role": message.role,
        "created_at": str(message.created_at),
        "telegram_message_id": message.telegram_message_id,
        "input_type": message.input_type,
        "content_hash": _hash_text(message.content),
        "metadata": {key: meta.get(key) for key in safe_meta_keys},
    }


@router.get("/ops/image-acceptance-b71e4c9fd2a658")
def image_acceptance(x_ops_token: str | None = Header(default=None)) -> dict:
    if x_ops_token != _TOKEN:
        raise HTTPException(status_code=404, detail="not found")
    db = SessionLocal()
    try:
        jobs = db.scalars(
            select(ImageGenerationJob)
            .order_by(ImageGenerationJob.id.desc())
            .limit(30)
        ).all()
        user_ids = sorted({job.user_id for job in jobs[:12]})
        messages = []
        if user_ids:
            messages = db.scalars(
                select(Message)
                .where(Message.user_id.in_(user_ids))
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(120)
            ).all()
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "jobs": [_safe_job(db, job) for job in jobs],
            "messages": [_safe_message(message) for message in messages],
        }
    finally:
        db.close()
