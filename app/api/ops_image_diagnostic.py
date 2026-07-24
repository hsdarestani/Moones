from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.llm.image_client import VeniceImageClient
from app.models.image_generation import ImageGenerationJob

router = APIRouter()

_OPS_TOKEN = "moones-imgdiag-20260724-7f9d8e31c41a6b25"


def _job_snapshot(job: ImageGenerationJob) -> dict[str, Any]:
    metadata = job.metadata_json or {}
    attempts = metadata.get("provider_model_attempts") or []
    return {
        "id": job.id,
        "user_id": job.user_id,
        "status": job.status,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "model_column": job.model,
        "attempt_count": job.attempt_count,
        "created_at": str(job.created_at),
        "started_at": str(job.started_at),
        "failed_at": str(job.failed_at),
        "primary_generation_model": metadata.get("primary_generation_model"),
        "fallback_generation_model": metadata.get("fallback_generation_model"),
        "configured_generation_model_plan": metadata.get("configured_generation_model_plan"),
        "effective_generation_model_plan": metadata.get("effective_generation_model_plan"),
        "skipped_unavailable_generation_models": metadata.get("skipped_unavailable_generation_models"),
        "provider_model_attempts": attempts,
        "last_provider_error_model": metadata.get("last_provider_error_model"),
        "last_provider_error_code": metadata.get("last_provider_error_code"),
        "final_generation_model": metadata.get("final_generation_model"),
        "final_qa_reason_codes": metadata.get("final_qa_reason_codes"),
    }


@router.get("/ops/image-diagnostic-7f9d8e31c41a6b25")
async def image_diagnostic(x_ops_token: str | None = Header(default=None)) -> dict[str, Any]:
    if x_ops_token != _OPS_TOKEN:
        raise HTTPException(status_code=404, detail="not found")

    settings = get_settings()
    db = SessionLocal()
    try:
        jobs = db.scalars(
            select(ImageGenerationJob)
            .order_by(ImageGenerationJob.id.desc())
            .limit(8)
        ).all()
        job_data = [_job_snapshot(job) for job in jobs]
    finally:
        db.close()

    client = VeniceImageClient(max_attempts=1)
    discovery: dict[str, Any]
    try:
        models = await client.available_image_models(ttl_seconds=1)
        discovery = {
            "ok": True,
            "models": sorted(models or []),
        }
    except Exception as exc:
        discovery = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
        }

    smoke: list[dict[str, Any]] = []
    for model in (
        getattr(settings, "image_generation_model", None) or "seedream-v5-lite",
        getattr(settings, "image_generation_fallback_model", None) or "venice-sd35",
    ):
        try:
            result = await client.generate(
                "A realistic casual smartphone photo of one fictional adult wearing ordinary clothes in daylight, no text, no watermark",
                "text, watermark, extra people, distorted anatomy",
                width=1024,
                height=1280,
                seed=24681357,
                model=model,
            )
            smoke.append(
                {
                    "model": model,
                    "ok": True,
                    "mime_type": result.mime_type,
                    "byte_size": len(result.image_bytes),
                    "response_type": result.response_type,
                    "request_id": result.request_id,
                    "metadata": result.metadata,
                }
            )
            break
        except Exception as exc:
            smoke.append(
                {
                    "model": model,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1500],
                }
            )

    return {
        "settings": {
            "venice_api_key_present": bool(settings.venice_api_key),
            "venice_api_base_url": settings.venice_api_base_url,
            "image_generation_model": getattr(settings, "image_generation_model", None),
            "image_generation_fallback_model": getattr(settings, "image_generation_fallback_model", None),
            "image_generation_adult_model": getattr(settings, "image_generation_adult_model", None),
            "image_generation_adult_fallback_model": getattr(settings, "image_generation_adult_fallback_model", None),
        },
        "discovery": discovery,
        "smoke": smoke,
        "jobs": job_data,
    }
