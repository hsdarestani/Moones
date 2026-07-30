from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.image_generation import ImageGenerationJob

router = APIRouter()
_TOKEN = "moones-jobdiag-20260730-4f3c8d9a7b2e61"


def _safe_job(job: ImageGenerationJob) -> dict:
    meta = dict(getattr(job, "metadata_json", None) or {})
    requirements = dict(meta.get("visual_requirements") or {})
    contract = dict(requirements.get("photo_contract") or {})
    qa = dict(meta.get("generated_image_qa") or meta.get("final_qa") or {})
    return {
        "id": getattr(job, "id", None),
        "status": getattr(job, "status", None),
        "image_action": str(getattr(job, "image_action", None)),
        "source_image_job_id": getattr(job, "source_image_job_id", None),
        "attempt_count": getattr(job, "attempt_count", None),
        "model": getattr(job, "model", None),
        "error_code": getattr(job, "error_code", None),
        "error_message": str(getattr(job, "error_message", None) or "")[:300],
        "created_at": str(getattr(job, "created_at", None)),
        "sent_at": str(getattr(job, "sent_at", None)),
        "failed_at": str(getattr(job, "failed_at", None)),
        "route_action": meta.get("route_action"),
        "selected_generation_model": meta.get("selected_generation_model"),
        "final_generation_model": meta.get("final_generation_model"),
        "framing": meta.get("framing") or requirements.get("framing_requirement"),
        "full_body_visible": requirements.get("full_body_visible"),
        "head_visible": requirements.get("head_visible"),
        "feet_visible": requirements.get("feet_visible"),
        "body_not_cropped": requirements.get("body_not_cropped"),
        "camera_mode": requirements.get("camera_mode") or contract.get("camera_mode"),
        "identity_consistency_required": contract.get("identity_consistency_required"),
        "final_qa_reason_codes": meta.get("final_qa_reason_codes") or qa.get("reason_codes"),
        "provider_model_attempts": meta.get("provider_model_attempts"),
        "last_provider_error_code": meta.get("last_provider_error_code"),
        "last_provider_error_model": meta.get("last_provider_error_model"),
    }


@router.get("/ops/image-jobs-4f3c8d9a7b2e61")
def image_jobs(x_ops_token: str | None = Header(default=None)) -> dict:
    if x_ops_token != _TOKEN:
        raise HTTPException(status_code=404, detail="not found")
    db = SessionLocal()
    try:
        jobs = db.scalars(
            select(ImageGenerationJob)
            .order_by(ImageGenerationJob.id.desc())
            .limit(16)
        ).all()
        return {"jobs": [_safe_job(job) for job in jobs]}
    finally:
        db.close()
