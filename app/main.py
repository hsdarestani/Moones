from fastapi import FastAPI

from app.api.telegram import router as telegram_router
from app.core.config import get_settings
from app.core.logger import configure_logging

configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(telegram_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}
