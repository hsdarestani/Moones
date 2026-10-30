"""Verified model-catalog pricing for optional Venice embeddings.

Never infer embedding USD charges from LLM token pricing. Catalog values are
USD per *million* input tokens. Fail closed if price isn't available.
"""
from __future__ import annotations

import logging
import math
import time
from decimal import Decimal, InvalidOperation

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_CACHED: dict[str, tuple[float, Decimal]] = {}
_CACHE_TTL_SECONDS = 1800


def catalog_input_rate(payload: dict, model_id: str) -> Decimal | None:
    """Read the exact selected model's input.usd price (per million tokens)."""
    items = payload.get("data")
    if not isinstance(items, list):
        return None
    for model in items:
        if not isinstance(model, dict) or model.get("id") != model_id:
            continue
        price = ((model.get("model_spec") or {}).get("pricing") or {}).get("input") or {}
        raw = price.get("usd") if isinstance(price, dict) else None
        try:
            rate = Decimal(str(raw))
            if rate.is_finite() and rate > 0 and rate <= Decimal("1000"):
                return rate
        except (InvalidOperation, TypeError, ValueError):
            pass
        return None
    return None


def cost_from_usage(payload: dict, rate_per_million: Decimal) -> tuple[int | None, Decimal | None]:
    """Use provider-reported prompt tokens; never silently estimate text tokens."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    tokens = usage.get("prompt_tokens", usage.get("total_tokens"))
    if isinstance(tokens, bool):
        return None, None
    try:
        value = int(tokens)
        if value < 0 or tokens is None or value != float(tokens):
            return None, None
    except (TypeError, ValueError, OverflowError):
        return None, None
    cost = Decimal(value) * rate_per_million / Decimal(1_000_000)
    return value, cost


async def current_embedding_rate(model_id: str, *, client: httpx.AsyncClient | None = None) -> Decimal | None:
    """Get the live USD catalog rate; cache for 30 minutes per configured model."""
    import os
    now = time.monotonic()
    cached = _CACHED.get(model_id)
    if cached and now < cached[0]:
        return cached[1]
    cfg = get_settings()
    if not cfg.venice_api_key:
        return None
    url = cfg.venice_api_base_url.rstrip("/") + "/models"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=4.0) as http:
                response = await http.get(url, params={"type": "embedding"},
                                          headers={"Authorization": f"Bearer {cfg.venice_api_key}"})
        else:
            response = await client.get(url, params={"type": "embedding"},
                                        headers={"Authorization": f"Bearer {cfg.venice_api_key}"})
        response.raise_for_status()
        rate = catalog_input_rate(response.json(), model_id)
        if rate is not None:
            _CACHED[model_id] = (now + _CACHE_TTL_SECONDS, rate)
        return rate
    except Exception as exc:
        logger.warning("EMBEDDING_CATALOG_UNAVAILABLE error_type=%s", type(exc).__name__)
        return None


def current_embedding_rate_sync(model_id: str) -> Decimal | None:
    """Manual indexing price gate; never call provider to embed without a price."""
    now = time.monotonic()
    cached = _CACHED.get(model_id)
    if cached and now < cached[0]:
        return cached[1]
    cfg = get_settings()
    if not cfg.venice_api_key:
        return None
    try:
        with httpx.Client(timeout=4.0) as client:
            response = client.get(cfg.venice_api_base_url.rstrip("/") + "/models",
                                  params={"type": "embedding"},
                                  headers={"Authorization": f"Bearer {cfg.venice_api_key}"})
        response.raise_for_status()
        rate = catalog_input_rate(response.json(), model_id)
        if rate is not None:
            _CACHED[model_id] = (now + _CACHE_TTL_SECONDS, rate)
        return rate
    except Exception as exc:
        logger.warning("EMBEDDING_CATALOG_UNAVAILABLE error_type=%s", type(exc).__name__)
        return None
