import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.addon import AddonProduct, UserAddon
from app.models.billing import UsageCharge
from app.models.image_generation import (
    ImageGenerationArtifact,
    ImageGenerationFeedback,
    ImageGenerationJob,
    PartnerVisualProfile,
)
from app.models.memory import MemoryItem
from app.models.usage import AiUsageEvent
from app.models.user import User
from app.models.wallet import Wallet, WalletTransaction


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Wallet.__table__,
            WalletTransaction.__table__,
            AddonProduct.__table__,
            UserAddon.__table__,
            UsageCharge.__table__,
            AiUsageEvent.__table__,
            PartnerVisualProfile.__table__,
            ImageGenerationJob.__table__,
            ImageGenerationArtifact.__table__,
            ImageGenerationFeedback.__table__,
            MemoryItem.__table__,
        ],
    )
    return sessionmaker(bind=engine)()


def test_terminal_failure_text_is_natural_and_specific():
    from app.services.image_generation_service import terminal_image_failure_text

    text = terminal_image_failure_text(
        SimpleNamespace(status="failed", error_code="provider_failure")
    )
    assert "سکه" in text
    assert "سرویس" in text


def test_safe_status_send_swallows_telegram_failure():
    from app.services.image_generation_service import _safe_send_image_status

    class Telegram:
        async def send_text(self, *args, **kwargs):
            raise RuntimeError("telegram down")

    asyncio.run(_safe_send_image_status(Telegram(), 1, "x"))


def test_failed_job_status_copy_after_eighteen_minutes():
    from app.services.partner_photo_contract import image_status_text

    assert image_status_text("failed", "provider_failure")


def test_failed_job_followup_after_eighteen_minutes_routes_to_status():
    from app.services.semantic_image_intent_router import (
        RecentImageJobSummary,
        SemanticImageAction,
        SemanticImageDecision,
        SemanticImageRouterContext,
        resolve_active_image_job_followup_semantically,
    )

    class Client:
        async def complete_result(self, *args, **kwargs):
            return SimpleNamespace(
                text='{"action":"status_query","confidence":0.98}'
            )

    failed_at = datetime.utcnow() - timedelta(minutes=18)
    context = SemanticImageRouterContext(
        current_user_message="چیشد پس",
        latest_image_job=RecentImageJobSummary(
            job_id=145,
            status="failed",
            action="generate_new",
            failed_at=failed_at.isoformat(),
            error_code="provider_failure",
        ),
    )
    initial = SemanticImageDecision(
        action=SemanticImageAction.CHAT,
        media_delivery_requested=False,
        confidence=0.8,
        reason_code="chat",
    )
    model = SimpleNamespace(
        client=Client(),
        model="test",
        timeout_seconds=1,
    )

    resolved = asyncio.run(
        resolve_active_image_job_followup_semantically(
            context,
            initial,
            model=model,
        )
    )

    assert resolved.action == SemanticImageAction.STATUS_QUERY


def test_stale_processing_job_is_recovered():
    from app.services.image_generation_service import claim_next_job

    db = _session()
    user = User(telegram_id=99001)
    db.add(user)
    db.flush()
    job = ImageGenerationJob(
        idempotency_key="stale-job",
        correlation_id="c",
        user_id=user.id,
        chat_id=99,
        source_telegram_message_id=77,
        status="processing",
        scheduled_at=datetime.utcnow() - timedelta(minutes=10),
        lock_expires_at=datetime.utcnow() - timedelta(seconds=1),
        user_request="عکس بده",
        prompt="p",
        negative_prompt="n",
        model="m",
        width=1024,
        height=1024,
        steps=1,
        cfg_scale=1,
        seed=1,
    )
    db.add(job)
    db.commit()

    claimed = claim_next_job(db)

    assert claimed.id == job.id
    assert claimed.status == "processing"
    assert claimed.attempt_count == 1
    assert (claimed.metadata_json or {}).get("stale_worker_recovered_at")
    assert (claimed.metadata_json or {}).get("stale_worker_previous_status") == "processing"
