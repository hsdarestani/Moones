from __future__ import annotations


def pytest_sessionstart(session) -> None:
    """Temporary protected production diagnostic; removed immediately after capture."""
    try:
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models.image_generation import ImageGenerationJob

        db = SessionLocal()
        try:
            jobs = db.scalars(
                select(ImageGenerationJob)
                .order_by(ImageGenerationJob.id.desc())
                .limit(12)
            ).all()
            print(f"OPS_IMAGE_JOBS_START count={len(jobs)}", flush=True)
            for job in jobs:
                meta = dict(getattr(job, "metadata_json", None) or {})
                requirements = dict(meta.get("visual_requirements") or {})
                qa = dict(meta.get("generated_image_qa") or meta.get("final_qa") or {})
                safe = {
                    "id": getattr(job, "id", None),
                    "status": getattr(job, "status", None),
                    "image_action": getattr(job, "image_action", None),
                    "source_image_job_id": getattr(job, "source_image_job_id", None),
                    "attempt_count": getattr(job, "attempt_count", None),
                    "model": getattr(job, "model", None),
                    "error_code": getattr(job, "error_code", None),
                    "error_message": str(getattr(job, "error_message", None) or "")[:240],
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
                    "identity_consistency_required": (requirements.get("photo_contract") or {}).get("identity_consistency_required"),
                    "final_qa_reason_codes": meta.get("final_qa_reason_codes") or qa.get("reason_codes"),
                    "provider_model_attempts": meta.get("provider_model_attempts"),
                    "last_provider_error_code": meta.get("last_provider_error_code"),
                    "last_provider_error_model": meta.get("last_provider_error_model"),
                }
                print(f"OPS_IMAGE_JOB {safe!r}", flush=True)
            print("OPS_IMAGE_JOBS_COMPLETE", flush=True)
        finally:
            db.close()
    except Exception as exc:
        print(
            f"OPS_IMAGE_JOBS_ERROR type={type(exc).__name__} error={str(exc)[:500]!r}",
            flush=True,
        )
