import asyncio
import logging
import random
from datetime import datetime
from contextlib import suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logger import configure_logging
from app.db.session import SessionLocal
from app.services.proactive_service import ProactiveService
from app.services.partner_life_service import PartnerLifeService
from app.services.style_audit import run_persian_audit
from app.services.human_delivery_service import HumanDeliveryService
from app.services.delayed_reaction_service import DelayedReactionService
from app.services.image_generation_service import claim_next_job, process_job, cleanup_stale_artifacts
from app.services.telegram_service import TelegramService

configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(telegram_router)
app.include_router(admin_router)
logger = logging.getLogger(__name__)
_proactive_task: asyncio.Task | None = None
_human_delivery_task: asyncio.Task | None = None
_delayed_reaction_task: asyncio.Task | None = None
_image_generation_task: asyncio.Task | None = None


async def _proactive_tick(service: ProactiveService) -> tuple[int, int]:
    db = SessionLocal()
    selected_count = 0
    sent_count = 0
    started_at = datetime.utcnow()
    logger.info("PROACTIVE_TICK started_at=%s", started_at.isoformat())
    try:
        if started_at.hour in {2, 14}:
            await PartnerLifeService().run_due(db, limit=20)
            run_persian_audit(db, limit=200)
            db.commit()
        users = service.eligible_users(db, limit=10)
        selected_count = len(users)
        for user in users:
            if await service.send_one(db, user):
                sent_count += 1
            db.commit()
        logger.info("PROACTIVE_TICK_FINISHED selected_count=%s sent_count=%s", selected_count, sent_count)
        return selected_count, sent_count
    except Exception:
        logger.exception("PROACTIVE_MESSAGE_SKIPPED reason=scheduler_error")
        db.rollback()
        return selected_count, sent_count
    finally:
        db.close()


async def _human_delivery_loop() -> None:
    service = HumanDeliveryService()
    logger.info("HUMAN_DELIVERY_SCHEDULER_STARTED tick_seconds=3")
    while True:
        db = SessionLocal()
        try:
            await service.run_due_jobs(db, limit=20)
            db.commit()
        except Exception:
            logger.exception("HUMAN_DELIVERY_JOB_FAILED reason=scheduler_error")
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(3)


async def _delayed_reaction_loop() -> None:
    service = DelayedReactionService()
    tick_seconds = 5
    logger.info("DELAYED_REACTION_SCHEDULER_STARTED tick_seconds=%s", tick_seconds)
    while True:
        db = SessionLocal()
        try:
            await service.process_due_jobs(db, limit=10)
            db.commit()
        except Exception:
            logger.exception("DELAYED_REACTION_FAILED reason=scheduler_error")
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(tick_seconds)


async def _image_generation_loop() -> None:
    tick_seconds = 3
    image_telegram_service = TelegramService("chat")
    if not image_telegram_service.token:
        logger.error("IMAGE_GENERATION_WORKER_BLOCKED missing_chat_bot_token=true")
        return
    logger.info("IMAGE_GENERATION_WORKER_STARTED tick_seconds=%s", tick_seconds)
    while True:
        db = SessionLocal()
        try:
            job = claim_next_job(db)
            if job:
                await process_job(db, job, telegram_service=image_telegram_service)
            cleanup_stale_artifacts(db, older_than_hours=6)
            db.commit()
        except Exception:
            logger.exception("IMAGE_GENERATION_WORKER_ERROR")
            db.rollback()
        finally:
            db.close()
        await asyncio.sleep(tick_seconds)


async def _proactive_loop() -> None:
    service = ProactiveService()
    db = SessionLocal()
    try:
        tick_seconds = service.scheduler_tick_seconds(db)
    finally:
        db.close()
    logger.info("PROACTIVE_SCHEDULER_STARTED tick_seconds=%s", tick_seconds)
    await asyncio.sleep(random.randint(5, 15))
    while True:
        await _proactive_tick(service)
        await asyncio.sleep(tick_seconds)


@app.on_event("startup")
async def start_proactive_scheduler() -> None:
    global _proactive_task, _human_delivery_task, _delayed_reaction_task, _image_generation_task
    _proactive_task = asyncio.create_task(_proactive_loop())
    _human_delivery_task = asyncio.create_task(_human_delivery_loop())
    _delayed_reaction_task = asyncio.create_task(_delayed_reaction_loop())
    _image_generation_task = asyncio.create_task(_image_generation_loop())


@app.on_event("shutdown")
async def stop_proactive_scheduler() -> None:
    for task in (_proactive_task, _human_delivery_task, _delayed_reaction_task, _image_generation_task):
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
