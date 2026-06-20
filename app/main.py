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

configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(telegram_router)
app.include_router(admin_router)
logger = logging.getLogger(__name__)
_proactive_task: asyncio.Task | None = None


async def _proactive_tick(service: ProactiveService) -> tuple[int, int]:
    db = SessionLocal()
    selected_count = 0
    sent_count = 0
    started_at = datetime.utcnow()
    logger.info("PROACTIVE_TICK started_at=%s", started_at.isoformat())
    try:
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
    global _proactive_task
    _proactive_task = asyncio.create_task(_proactive_loop())


@app.on_event("shutdown")
async def stop_proactive_scheduler() -> None:
    if _proactive_task:
        _proactive_task.cancel()
        with suppress(asyncio.CancelledError):
            await _proactive_task


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
