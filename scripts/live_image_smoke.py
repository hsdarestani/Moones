"""Explicit, bounded live Venice image smoke.

This script is deliberately NOT a pytest test and is never invoked by normal CI
or deploy test runs. It can make at most one paid image-generation request.

Required opt-in:
    MOONES_ALLOW_PAID_IMAGE_SMOKE=YES_I_ACCEPT_ONE_PAID_IMAGE

Optional model override:
    MOONES_LIVE_IMAGE_SMOKE_MODEL=krea-2-turbo
    MOONES_LIVE_IMAGE_SMOKE_MODEL=seedream-v5-lite
"""

from __future__ import annotations

import asyncio
import hashlib
import os


OPT_IN_ENV = "MOONES_ALLOW_PAID_IMAGE_SMOKE"
OPT_IN_VALUE = "YES_I_ACCEPT_ONE_PAID_IMAGE"
MODEL_ENV = "MOONES_LIVE_IMAGE_SMOKE_MODEL"
ALLOWED_MODELS = {"krea-2-turbo", "seedream-v5-lite"}


async def _run() -> None:
    if os.getenv(OPT_IN_ENV) != OPT_IN_VALUE:
        raise SystemExit(
            "Refusing paid live image smoke: explicit opt-in missing. "
            f"Set {OPT_IN_ENV}={OPT_IN_VALUE} only when one paid image request is intended."
        )

    from app.core.config import get_settings
    from app.llm.image_client import VeniceImageClient
    from app.services.provider_error_screen_detector import detect_provider_error_screen

    settings = get_settings()
    if not settings.venice_api_key:
        raise SystemExit("Venice API key is not configured.")

    model = str(os.getenv(MODEL_ENV) or "krea-2-turbo").strip()
    if model not in ALLOWED_MODELS:
        raise SystemExit(f"Unsupported smoke model: {model}")

    client = VeniceImageClient()
    available = await client.available_image_models(ttl_seconds=30)
    if available is not None and model not in available:
        raise SystemExit(f"Requested smoke model is unavailable: {model}")

    # One provider generation only. No fallback, retry generation, visual QA,
    # anatomy QA, or reviewer fan-out is allowed in this smoke.
    result = await client.generate(
        "Photorealistic ordinary personal photo of one fictional adult standing in a quiet city setting at night, natural lighting, no text or logo.",
        "text, watermark, logo, duplicate person, malformed anatomy",
        width=1024,
        height=1280,
        seed=48151623,
        model=model,
    )

    detection = detect_provider_error_screen(result.image_bytes)
    if detection.is_error_screen:
        raise SystemExit(f"Provider returned an error/moderation artifact: {detection.reason}")

    checksum = hashlib.sha256(result.image_bytes).hexdigest()[:12]
    print(
        f"LIVE_IMAGE_SMOKE_OK model={model} bytes={len(result.image_bytes)} checksum={checksum}",
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(_run())
