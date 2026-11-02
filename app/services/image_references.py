"""Use only this account's own delivered fictional-character image as reference."""
from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models.image_generation import ImageGenerationArtifact, ImageGenerationJob, PartnerVisualProfile


def remember_reference(db: Session, job: ImageGenerationJob, file_id: str | None) -> None:
    if not file_id or job.status != "sent" or job.content_mode != "normal" or not job.identity_fingerprint:
        return
    meta = job.metadata_json or {}
    if meta.get("adult_intent_detected") or int(meta.get("expected_subject_count") or 1) != 1:
        return
    if "partner_visual_profiles" not in inspect(db.connection()).get_table_names():
        return
    profile = db.scalar(select(PartnerVisualProfile).where(PartnerVisualProfile.user_id == job.user_id))
    if profile is None:
        return
    data = dict(profile.profile_json or {})
    current = data.get("canonical_reference") or {}
    if current.get("identity_fingerprint") == job.identity_fingerprint:
        return  # Stable anchor; new scenes must not progressively replace it.
    data["canonical_reference"] = {"job_id": job.id, "file_id": file_id,
                                    "identity_fingerprint": job.identity_fingerprint}
    profile.profile_json = data


async def reference_for_job(db: Session, job: ImageGenerationJob, telegram_service) -> tuple[bytes | None, int | None]:
    if "partner_visual_profiles" not in inspect(db.connection()).get_table_names():
        return None, None
    profile = db.scalar(select(PartnerVisualProfile).where(PartnerVisualProfile.user_id == job.user_id))
    reference = ((profile.profile_json or {}).get("canonical_reference") or {}) if profile else {}
    if not reference or reference.get("identity_fingerprint") != job.identity_fingerprint:
        return None, None
    source = db.scalar(select(ImageGenerationJob).where(
        ImageGenerationJob.id == reference.get("job_id"),
        ImageGenerationJob.user_id == job.user_id, ImageGenerationJob.chat_id == job.chat_id,
        ImageGenerationJob.status == "sent", ImageGenerationJob.content_mode == "normal",
        ImageGenerationJob.identity_fingerprint == job.identity_fingerprint,
    ))
    if source is None or not reference.get("file_id"):
        return None, None
    artifact = db.scalar(select(ImageGenerationArtifact).where(ImageGenerationArtifact.job_id == source.id))
    if artifact and artifact.image_bytes:
        return artifact.image_bytes, source.id
    # Never hand a Telegram URL (which contains the bot token) to a provider.
    path = await telegram_service.get_file_path(reference["file_id"])
    with tempfile.TemporaryDirectory(prefix="moones-reference-") as folder:
        target = Path(folder) / "reference.image"
        count = await telegram_service.download_file(path, str(target))
        if not 0 < count <= 8_000_000:
            raise ValueError("reference_size_invalid")
        return target.read_bytes(), source.id
