import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace


def test_terminal_failure_text_is_natural_and_specific():
    from app.services.image_generation_service import terminal_image_failure_text
    assert "سکه" in terminal_image_failure_text(SimpleNamespace(status="failed", error_code="provider_failure"))
    assert "سرویس" in terminal_image_failure_text(SimpleNamespace(status="failed", error_code="provider_failure"))


def test_safe_status_send_swallows_telegram_failure():
    from app.services.image_generation_service import _safe_send_image_status
    class Telegram:
        async def send_text(self, *args, **kwargs):
            raise RuntimeError("telegram down")
    asyncio.run(_safe_send_image_status(Telegram(), 1, "x"))


def test_failed_job_status_copy_after_eighteen_minutes():
    from app.services.partner_photo_contract import image_status_text
    assert image_status_text("failed", "provider_failure")


def test_stale_processing_job_is_recovered(db_session, user):
    from app.models.image_generation import ImageGenerationJob
    from app.services.image_generation_service import claim_next_job
    job=ImageGenerationJob(idempotency_key="stale-job", correlation_id="c", user_id=user.id, chat_id=99, source_telegram_message_id=77, status="processing", scheduled_at=datetime.utcnow()-timedelta(minutes=10), lock_expires_at=datetime.utcnow()-timedelta(seconds=1), user_request="عکس بده", prompt="p", negative_prompt="n", model="m", width=1024, height=1024, steps=1, cfg_scale=1, seed=1)
    db_session.add(job); db_session.commit()
    claimed=claim_next_job(db_session)
    assert claimed.id == job.id
    assert claimed.status == "processing"
    assert (claimed.metadata_json or {}).get("stale_worker_recovered_at")
