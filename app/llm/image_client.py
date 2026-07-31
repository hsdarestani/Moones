from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_MODEL = "seedream-v5-lite"
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1280
DEFAULT_STEPS = 45
DEFAULT_CFG_SCALE = 4
VENICE_SEED_MIN = 1
VENICE_SEED_MAX = 999_999_999
DEFAULT_SEED = VENICE_SEED_MIN
MAX_PROVIDER_IMAGE_BYTES = 12_000_000
SUPPORTED_IMAGE_DIMENSIONS = {(1024, 1280), (1280, 1024)}

_RESOLUTION_TIER_MODELS = {
    "seedream-v5-lite",
    "grok-imagine-image",
    "grok-imagine-image-quality",
    "gpt-image-2",
    "nano-banana-2",
    "nano-banana-pro",
}
_ASPECT_RATIO_MODELS = {"qwen-image-2", "qwen-image-2-pro"}
_MODEL_CACHE_IDS: set[str] | None = None
_MODEL_CACHE_EXPIRES_AT = 0.0
_MODEL_CACHE_LOCK = asyncio.Lock()

# Venice enforces model-specific prompt limits. Keep the authoritative internal
# plan intact for validation/QA while adapting only the provider-bound strings.
# Krea's documented live limit is 5,000 characters; use headroom for any provider
# normalization. Legacy Lustify models need much tighter practical prompts.
PROVIDER_POSITIVE_PROMPT_LIMITS = {
    "krea-2-turbo": 4800,
    "lustify-sdxl": 1200,
    "lustify-v7": 1200,
    "lustify-v8": 1200,
}
PROVIDER_NEGATIVE_PROMPT_LIMITS = {
    "krea-2-turbo": 4800,
    "lustify-sdxl": 1200,
    "lustify-v7": 1200,
    "lustify-v8": 1200,
}


def _trim_at_word_boundary(value: str, max_chars: int) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= max_chars:
        return value
    clipped = value[: max(1, max_chars - 1)].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip(" ,;:.-") + "."


def _compact_positive_prompt(prompt: str, max_chars: int) -> str:
    prompt = " ".join(str(prompt or "").split())
    if len(prompt) <= max_chars:
        return prompt

    lower = prompt.lower()
    essentials: list[str] = []

    if "exactly one fictional adult" in lower:
        essentials.append("Exactly one fictional adult; no minors and no additional people.")
    if "visibly fully nude" in lower or "full nudity" in lower:
        essentials.append("The fictional adult is visibly fully nude; no clothing, underwear, lingerie, or covered requested body regions.")
    if "complete full figure visible from head to feet" in lower or "entire body inside frame" in lower:
        essentials.append("Complete full figure visible from head to feet, entire body inside frame, camera far enough away, no cropped head, torso, knees, legs, or feet.")
    if "mirror_selfie" in lower or "mirror selfie" in lower or "mirror" in lower:
        essentials.append("Natural full-body mirror selfie in a private indoor room; mirror visible, plausible phone-camera geometry, and no distinct extra person in the reflection.")
    elif "private_indoor" in lower or "private indoor" in lower:
        essentials.append("Private indoor setting must be clearly visible.")
    if "identity lock" in lower or "stored fingerprint" in lower or "same recognizable person" in lower:
        essentials.append("Preserve the same stored fictional adult identity as the canonical identity: core face geometry, eye shape and spacing, eyebrows, nose geometry, jaw/chin structure, stable distinguishing features, core hair color/texture, skin tone, and body-build family.")
    age_match=re.search(r"fictional age\s+(\d{2,3})", prompt, re.I)
    if age_match:
        essentials.append(f"Mutable profile overlay: render this same canonical person at fictional age {age_match.group(1)}; age appearance may change but the canonical facial identity must not be redesigned or replaced.")
    for anatomy in ("female", "male", "intersex"):
        if f"consistently {anatomy}" in lower:
            essentials.append(f"Consistent natural {anatomy} adult anatomy; no contradictory, mixed, malformed, duplicated, ambiguous, or impossible structure.")
            break
    if "photorealistic" in lower or "natural skin texture" in lower or "believable personal-photo" in lower:
        essentials.append("Photorealistic believable personal photo with natural skin texture, natural lighting, realistic posture, and no artificial catalogue look.")
    if "visible floor below both feet" in lower or "subject no more than about 70 percent" in lower:
        essentials.append("Corrective full-body composition: full body visible and full figure head-to-feet inside a portrait 4:5 mirror frame, with headroom above the hair, visible floor below both feet, both feet fully visible, subject at most about 70 percent of frame height, camera farther away, no close-up and no crop.")

    segments = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", prompt) if segment.strip()]
    priority_markers = (
        "recurring fictional partner",
        "identity lock",
        "canonical identity lock",
        "mutable profile overlay",
        "scene:",
        "location:",
        "activity:",
        "pose:",
        "support surface:",
        "camera mode:",
        "lighting:",
        "expression/features:",
        "user-requested visual details:",
        "strict partner-photo correction:",
        "correct the framing exactly:",
        "do not return a clothed",
        "preserve the exact stored face family",
        "preserve the stored adult identity and anatomical profile",
    )
    selected: list[str] = []
    seen: set[str] = set()

    def add(segment: str) -> None:
        normalized = " ".join(segment.split())
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        selected.append(normalized)

    for item in essentials:
        add(item)
    for marker in priority_markers:
        for segment in segments:
            if marker in segment.lower():
                add(segment)
    for segment in segments:
        add(segment)

    compact = ""
    for segment in selected:
        candidate = segment if not compact else compact + " " + segment
        if len(candidate) <= max_chars:
            compact = candidate
            continue
        if not compact:
            compact = _trim_at_word_boundary(segment, max_chars)
        break
    return _trim_at_word_boundary(compact or prompt, max_chars)


def _compact_negative_prompt(negative_prompt: str, max_chars: int) -> str:
    negative_prompt = " ".join(str(negative_prompt or "").split())
    if len(negative_prompt) <= max_chars:
        return negative_prompt
    terms = [term.strip() for term in negative_prompt.split(",") if term.strip()]
    priority_markers = (
        "child", "teen", "minor", "youth",
        "duplicate", "extra", "second person", "background person", "mirror duplicate",
        "malformed anatomy", "ambiguous anatomy", "contradictory anatomy", "mixed sex",
        "clothed", "shirt", "dress", "underwear", "lingerie", "covered",
        "close-up", "headshot", "cropped", "missing legs", "missing feet",
        "watermark", "text", "logo",
    )
    ordered = sorted(
        enumerate(terms),
        key=lambda pair: (
            -sum(1 for marker in priority_markers if marker in pair[1].lower()),
            pair[0],
        ),
    )
    out: list[str] = []
    for _, term in ordered:
        candidate = ", ".join(out + [term])
        if len(candidate) > max_chars:
            continue
        out.append(term)
    return ", ".join(out)


def adapt_provider_prompts(model: str, prompt: str, negative_prompt: str) -> tuple[str, str, dict]:
    normalized_model = str(model or "").strip()
    positive_limit = PROVIDER_POSITIVE_PROMPT_LIMITS.get(normalized_model)
    negative_limit = PROVIDER_NEGATIVE_PROMPT_LIMITS.get(normalized_model)
    compact_prompt = (
        _compact_positive_prompt(prompt, positive_limit)
        if positive_limit
        else prompt
    )
    compact_negative = (
        _compact_negative_prompt(negative_prompt, negative_limit)
        if negative_limit
        else negative_prompt
    )
    return compact_prompt, compact_negative, {
        "provider_prompt_compacted": compact_prompt != prompt or compact_negative != negative_prompt,
        "provider_prompt_limit": positive_limit,
        "provider_negative_prompt_limit": negative_limit,
        "original_prompt_chars": len(prompt or ""),
        "provider_prompt_chars": len(compact_prompt or ""),
        "original_negative_prompt_chars": len(negative_prompt or ""),
        "provider_negative_prompt_chars": len(compact_negative or ""),
    }


@dataclass
class ImageGenerationResponse:
    image_bytes: bytes
    mime_type: str
    request_id: str | None
    model: str
    width: int
    height: int
    latency_seconds: float
    response_type: str
    metadata: dict


class ImageClientError(Exception):
    retryable = False
    code = "image_error"


class ImageValidationError(ImageClientError):
    code = "validation"


class ImageAuthError(ImageClientError):
    code = "auth"


class ImageBalanceError(ImageClientError):
    code = "balance"


class ImageRateLimitError(ImageClientError):
    retryable = True
    code = "rate_limited"


class ImageProviderUnavailable(ImageClientError):
    retryable = True
    code = "provider_unavailable"


class ImageBadResponse(ImageClientError):
    code = "bad_response"


def _safe_provider_detail(resp: httpx.Response) -> str:
    try:
        detail = " ".join((resp.text or "").split())
    except Exception:
        detail = "unreadable_response"
    return (detail or "empty_response")[:500]


def image_resolution_tier(width: int, height: int) -> str:
    return "image_1k" if width * height <= 1024 * 1280 else "image_2k"


def validate_image_dimensions(
    width: int,
    height: int,
    *,
    model: str = DEFAULT_IMAGE_MODEL,
) -> tuple[int, int]:
    if (int(width), int(height)) not in SUPPORTED_IMAGE_DIMENSIONS:
        raise ImageValidationError(f"unsupported_dimensions:{width}x{height}")
    return int(width), int(height)


def normalize_venice_seed(seed: int | str | None, *, salt: str = "") -> tuple[int, bool]:
    requested = DEFAULT_SEED if seed is None else int(seed)
    if VENICE_SEED_MIN <= requested <= VENICE_SEED_MAX:
        return requested, False
    digest = int(hashlib.sha256(f"{requested}:{salt}".encode()).hexdigest(), 16)
    return VENICE_SEED_MIN + (digest % VENICE_SEED_MAX), True


def _aspect_ratio(width: int, height: int) -> str:
    return "4:5" if int(height) >= int(width) else "5:4"


def _payload_profile(model: str) -> str:
    if model in _RESOLUTION_TIER_MODELS:
        return "aspect_ratio_4_5_resolution_1k"
    if model in _ASPECT_RATIO_MODELS:
        return "aspect_ratio_4_5"
    return "pixel_1024x1280"


def build_venice_image_payload(
    *,
    model: str,
    prompt: str,
    negative_prompt: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    seed: int = DEFAULT_SEED,
) -> dict:
    provider_seed, _ = normalize_venice_seed(seed, salt=f"{model}:{width}x{height}")
    base = {
        "model": model,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "safe_mode": False,
        "seed": provider_seed,
        "return_binary": True,
    }
    if model == "krea-2-turbo":
        width, height = validate_image_dimensions(width, height, model=model)
        return {
            **base,
            "width": width,
            "height": height,
            "steps": DEFAULT_STEPS,
            "cfg_scale": DEFAULT_CFG_SCALE,
        }
    if model in _RESOLUTION_TIER_MODELS:
        return {
            **base,
            "aspect_ratio": _aspect_ratio(width, height),
            "resolution": "1K",
        }
    if model in _ASPECT_RATIO_MODELS:
        return {**base, "aspect_ratio": _aspect_ratio(width, height)}
    width, height = validate_image_dimensions(width, height, model=model)
    return {**base, "width": width, "height": height}


def venice_image_payload(
    prompt: str,
    negative_prompt: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    model: str = DEFAULT_IMAGE_MODEL,
    seed: int = DEFAULT_SEED,
) -> dict:
    return build_venice_image_payload(
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        seed=seed,
    )


def _endpoint(base: str) -> str:
    base = (base or "https://api.venice.ai/api/v1").rstrip("/") + "/"
    if base.endswith("/api/v1/"):
        return urljoin(base, "image/generate")
    return "https://api.venice.ai/api/v1/image/generate"


def _models_endpoint(base: str) -> str:
    base = (base or "https://api.venice.ai/api/v1").rstrip("/") + "/"
    if base.endswith("/api/v1/"):
        return urljoin(base, "models")
    return "https://api.venice.ai/api/v1/models"


def _extract_json_image(data: dict) -> tuple[bytes, str]:
    value = data.get("image") or data.get("image_base64")
    if value is None:
        images = data.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, str):
                value = first
            elif isinstance(first, dict):
                value = first.get("b64_json") or first.get("image") or first.get("image_base64")
    if value is None:
        items = data.get("data")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, str):
                value = first
            elif isinstance(first, dict):
                value = first.get("b64_json") or first.get("image") or first.get("image_base64")
        elif isinstance(items, str):
            value = items
    if not value:
        raise ImageBadResponse("missing_image")
    if isinstance(value, str) and value.startswith("data:"):
        header, value = value.split(",", 1)
        mime = header.split(";")[0].replace("data:", "") or "image/webp"
    else:
        mime = data.get("mime_type") or data.get("format") or "image/webp"
        if "/" not in mime:
            mime = f"image/{mime}"
    try:
        return base64.b64decode(value), mime
    except Exception as exc:
        raise ImageBadResponse("invalid_base64_image") from exc


def _validate(content: bytes, mime: str) -> None:
    if not mime.startswith("image/"):
        raise ImageBadResponse("invalid_mime")
    if not content or len(content) > MAX_PROVIDER_IMAGE_BYTES:
        raise ImageBadResponse("invalid_size")
    if content[:15].lower().startswith(b"<!doctype html") or content[:6].lower().startswith(b"<html>"):
        raise ImageBadResponse("html_body")


class VeniceImageClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
    ):
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.venice_api_key
        self.base_url = base_url or settings.venice_api_base_url
        self.client = client
        self.max_attempts = max_attempts

    async def available_image_models(self, *, ttl_seconds: int = 300) -> set[str] | None:
        global _MODEL_CACHE_IDS, _MODEL_CACHE_EXPIRES_AT
        now = time.monotonic()
        if _MODEL_CACHE_IDS is not None and now < _MODEL_CACHE_EXPIRES_AT:
            return set(_MODEL_CACHE_IDS)
        if not self.api_key:
            return None
        async with _MODEL_CACHE_LOCK:
            now = time.monotonic()
            if _MODEL_CACHE_IDS is not None and now < _MODEL_CACHE_EXPIRES_AT:
                return set(_MODEL_CACHE_IDS)
            try:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                if self.client is not None:
                    response = await self.client.get(
                        _models_endpoint(self.base_url),
                        params={"type": "image"},
                        headers=headers,
                    )
                else:
                    async with httpx.AsyncClient(timeout=10) as client:
                        response = await client.get(
                            _models_endpoint(self.base_url),
                            params={"type": "image"},
                            headers=headers,
                        )
                response.raise_for_status()
                data = response.json()
                model_ids = {
                    str(item.get("id"))
                    for item in (data.get("data") or [])
                    if isinstance(item, dict) and item.get("id")
                }
                if not model_ids:
                    raise ImageBadResponse("empty_image_model_list")
                _MODEL_CACHE_IDS = model_ids
                _MODEL_CACHE_EXPIRES_AT = now + max(30, int(ttl_seconds))
                logger.info("IMAGE_PROVIDER_MODELS_REFRESHED count=%s", len(model_ids))
                return set(model_ids)
            except Exception as exc:
                logger.warning("IMAGE_PROVIDER_MODEL_DISCOVERY_FAILED error_type=%s", type(exc).__name__)
                return None

    async def generate(
        self,
        prompt: str,
        negative_prompt: str,
        *,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        seed: int = DEFAULT_SEED,
        model: str = DEFAULT_IMAGE_MODEL,
    ) -> ImageGenerationResponse:
        if not self.api_key:
            raise ImageAuthError("missing_api_key")
        available_models = await self.available_image_models()
        if available_models is not None and model not in available_models:
            raise ImageValidationError(f"model_unavailable:{model}")

        provider_prompt, provider_negative_prompt, prompt_diagnostics = adapt_provider_prompts(
            model, prompt, negative_prompt
        )
        payload = build_venice_image_payload(
            model=model,
            prompt=provider_prompt,
            negative_prompt=provider_negative_prompt,
            width=width,
            height=height,
            seed=seed,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = _endpoint(self.base_url)
        timeout = httpx.Timeout(connect=10, read=120, write=30, pool=10)
        last: Exception | None = None
        seed_fallback_used = False

        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                if self.client is not None:
                    response = await self.client.post(url, json=payload, headers=headers)
                else:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.post(url, json=payload, headers=headers)

                if response.status_code in (400, 404, 415, 422):
                    detail = _safe_provider_detail(response)
                    if (
                        response.status_code == 400
                        and payload.get("seed") != DEFAULT_SEED
                        and not seed_fallback_used
                    ):
                        logger.warning(
                            "IMAGE_PROVIDER_SEED_FALLBACK status=%s detail=%s",
                            response.status_code,
                            detail,
                        )
                        payload = {**payload, "seed": DEFAULT_SEED}
                        seed_fallback_used = True
                        continue
                    raise ImageValidationError(f"{response.status_code}:{detail}")
                if response.status_code in (401, 403):
                    raise ImageAuthError(str(response.status_code))
                if response.status_code == 402:
                    raise ImageBalanceError("402")
                if response.status_code == 429:
                    raise ImageRateLimitError("429")
                if response.status_code in (500, 502, 503, 504):
                    raise ImageProviderUnavailable(str(response.status_code))
                if response.status_code >= 400:
                    raise ImageClientError(f"{response.status_code}:{_safe_provider_detail(response)}")

                content_type = (response.headers.get("content-type") or "").split(";")[0].lower()
                response_data: dict | None = None
                if content_type.startswith("image/"):
                    image_bytes = response.content
                    mime_type = content_type
                    response_type = "binary"
                elif content_type == "application/json":
                    response_data = response.json()
                    image_bytes, mime_type = _extract_json_image(response_data)
                    response_type = "json_base64"
                else:
                    raise ImageBadResponse(f"invalid_mime:{content_type or 'missing'}")
                _validate(image_bytes, mime_type)
                request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("request-id")
                    or (str(response_data.get("id")) if response_data and response_data.get("id") else None)
                )
                return ImageGenerationResponse(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    request_id=request_id,
                    model=model,
                    width=width,
                    height=height,
                    latency_seconds=time.monotonic() - started,
                    response_type=response_type,
                    metadata={
                        "seed_used": payload.get("seed"),
                        "seed_fallback_used": seed_fallback_used,
                        "payload_profile": _payload_profile(model),
                        **prompt_diagnostics,
                    },
                )
            except (httpx.TimeoutException, ImageRateLimitError, ImageProviderUnavailable) as exc:
                last = exc
                if attempt >= self.max_attempts:
                    raise ImageProviderUnavailable(str(exc)) from exc
                retry_after = response.headers.get("Retry-After") if "response" in locals() else None
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else (0.25 * (2 ** (attempt - 1)) + random.random() * 0.1)
                )
                await asyncio.sleep(delay)
        raise ImageProviderUnavailable(str(last))
