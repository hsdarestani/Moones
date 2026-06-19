import asyncio
import logging
from contextlib import suppress

from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logger import configure_logging
from app.db.session import SessionLocal
from app.services.proactive_service import ProactiveService

configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(telegram_router)
app.include_router(admin_router)
logger = logging.getLogger(__name__)
_proactive_task: asyncio.Task | None = None


async def _proactive_loop() -> None:
    service = ProactiveService()
    while True:
        await asyncio.sleep(900)
        db = SessionLocal()
        try:
            for user in service.eligible_users(db, limit=10):
                await service.send_one(db, user)
                db.commit()
        except Exception:
            logger.exception("PROACTIVE_MESSAGE_SKIPPED reason=scheduler_error")
            db.rollback()
        finally:
            db.close()


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
