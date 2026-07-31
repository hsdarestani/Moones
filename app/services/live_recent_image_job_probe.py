from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.image_generation import ImageGenerationJob


def run_live_recent_image_job_probe() -> None:
    cutoff = datetime.utcnow() - timedelta(hours=2)
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(ImageGenerationJob)
                .where(ImageGenerationJob.created_at >= cutoff)
                .order_by(ImageGenerationJob.id.desc())
                .limit(12)
            ).all()
        )
        for job in reversed(jobs):
            meta = dict(job.metadata_json or {})
            safe = {
                "job_id": job.id,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "status": job.status,
                "content_mode": job.content_mode,
                "model": job.model,
                "attempt_count": job.attempt_count,
                "error_code": job.error_code,
                "identity_locked_generation": meta.get("identity_locked_generation"),
                "configured_generation_model_plan": meta.get("configured_generation_model_plan"),
                "effective_generation_model_plan": meta.get("effective_generation_model_plan"),
                "effective_generation_attempt_plan": meta.get("effective_generation_attempt_plan"),
                "deferred_generation_models": meta.get("deferred_generation_models"),
                "skipped_unavailable_generation_models": meta.get("skipped_unavailable_generation_models"),
                "generated_image_quality_failures": meta.get("generated_image_quality_failures"),
                "provider_model_attempts": meta.get("provider_model_attempts"),
                "final_generation_model": meta.get("final_generation_model"),
                "final_qa_reason_codes": meta.get("final_qa_reason_codes"),
                "visual_requirements": {
                    k: (meta.get("visual_requirements") or {}).get(k)
                    for k in (
                        "partner_visible",
                        "explicit_nudity_requested",
                        "anatomy_qa_required",
                        "framing",
                        "camera_mode",
                    )
                },
            }
            print("REAL_IMAGE_JOB_DIAG " + json.dumps(safe, ensure_ascii=False, default=str), flush=True)
    raise RuntimeError("REAL_IMAGE_JOB_DIAG_COMPLETE_ROLLBACK")
