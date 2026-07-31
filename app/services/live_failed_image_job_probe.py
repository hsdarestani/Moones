from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from sqlalchemy import URL, create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.image_generation import ImageGenerationJob


def _production_database_url():
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise RuntimeError("PROD_DB_ENV_MISSING")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DB_USER") or "postgres",
        password=password,
        host=os.environ.get("DB_HOST") or "postgres",
        port=int(os.environ.get("DB_PORT") or 5432),
        database=os.environ.get("DB_NAME") or "mones",
    )


def run_live_failed_image_job_probe() -> None:
    engine = create_engine(_production_database_url(), pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    cutoff = datetime.utcnow() - timedelta(hours=4)
    with Session() as db:
        jobs = list(
            db.scalars(
                select(ImageGenerationJob)
                .where(
                    ImageGenerationJob.created_at >= cutoff,
                    ImageGenerationJob.error_code == "image_quality_single_subject_failed",
                )
                .order_by(ImageGenerationJob.id.desc())
                .limit(8)
            ).all()
        )
        for job in reversed(jobs):
            meta = dict(job.metadata_json or {})
            failures = []
            for failure in list(meta.get("generated_image_quality_failures") or []):
                failures.append(
                    {
                        "model": failure.get("model"),
                        "reason_codes": list(failure.get("reason_codes") or []),
                        "confidence": failure.get("confidence"),
                    }
                )
            safe = {
                "job_id": job.id,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                "model": job.model,
                "effective_generation_model_plan": meta.get("effective_generation_model_plan"),
                "effective_generation_attempt_plan": meta.get("effective_generation_attempt_plan"),
                "final_qa_reason_codes": list(meta.get("final_qa_reason_codes") or []),
                "generated_image_quality_failures": failures,
            }
            print("REAL_FAILED_IMAGE_JOB " + json.dumps(safe, ensure_ascii=False), flush=True)
    engine.dispose()
    raise RuntimeError("REAL_FAILED_IMAGE_JOB_PROBE_COMPLETE_ROLLBACK")
