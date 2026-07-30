from __future__ import annotations

import logging

from app.core.config import get_settings
from app.llm.image_client import VeniceImageClient

logger = logging.getLogger(__name__)


async def run_ops_provider_probe() -> None:
    """Run one protected live provider probe, log only safe metadata, then abort startup.

    The deployment wrapper is expected to roll back automatically because startup fails.
    """
    settings = get_settings()
    client = VeniceImageClient(max_attempts=1)

    configured_models: list[str] = []
    for value in (
        getattr(settings, "image_generation_model", None),
        getattr(settings, "image_generation_fallback_model", None),
        *((getattr(settings, "image_generation_emergency_models", "") or "").split(",")),
    ):
        model = str(value or "").strip()
        if model and model not in configured_models:
            configured_models.append(model)

    logger.warning(
        "OPS_VENICE_PROBE_START api_key_present=%s base_url=%s configured_models=%s",
        bool(settings.venice_api_key),
        settings.venice_api_base_url,
        configured_models,
    )

    try:
        models = await client.available_image_models(ttl_seconds=1)
        available = sorted(models or [])
        logger.warning(
            "OPS_VENICE_PROBE_DISCOVERY ok=true count=%s configured_available=%s sample=%s",
            len(available),
            [model for model in configured_models if model in set(available)],
            available[:20],
        )
    except Exception as exc:
        logger.exception(
            "OPS_VENICE_PROBE_DISCOVERY ok=false error_type=%s error=%s",
            type(exc).__name__,
            str(exc)[:1000],
        )

    success = False
    for model in configured_models:
        try:
            result = await client.generate(
                "A realistic casual smartphone portrait of one fictional adult woman indoors, fully clothed, natural daylight, no text, no watermark",
                "text, watermark, extra people, duplicated person, distorted anatomy",
                width=1024,
                height=1280,
                seed=24681357,
                model=model,
            )
            logger.warning(
                "OPS_VENICE_PROBE_RESULT model=%s ok=true mime=%s bytes=%s response_type=%s request_id=%s metadata=%s",
                model,
                result.mime_type,
                len(result.image_bytes),
                result.response_type,
                result.request_id,
                result.metadata,
            )
            success = True
            break
        except Exception as exc:
            logger.exception(
                "OPS_VENICE_PROBE_RESULT model=%s ok=false error_type=%s error_code=%s retryable=%s error=%s",
                model,
                type(exc).__name__,
                getattr(exc, "code", None),
                getattr(exc, "retryable", None),
                str(exc)[:1500],
            )

    logger.warning("OPS_VENICE_PROBE_COMPLETE success=%s", success)
    raise RuntimeError("OPS_VENICE_PROBE_COMPLETE")
