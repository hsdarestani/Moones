from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re


@dataclass(frozen=True)
class DetectionResult:
    is_error_screen: bool
    reason: str = ""
    confidence: str = "low"


_PROVIDER_ERROR_PATTERNS = (
    (re.compile(r"systems\s+have\s+detected\s+content.*violates\s+our\s+terms\s+of\s+service", re.I | re.S), "venice_terms_moderation_text"),
    (re.compile(r"please\s+try\s+changing\s+your\s+prompt.*trying\s+another\s+model", re.I | re.S), "venice_prompt_or_model_error_text"),
    (re.compile(r"contact\s+support@venice\.ai", re.I), "venice_support_policy_text"),
    (re.compile(r"moderation|policy\s+violation|violates\s+(our\s+)?terms\s+of\s+service|content\s+policy", re.I), "provider_policy_error_text"),
)


def _metadata_text(image_bytes: bytes) -> str:
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as im:
            parts = []
            for key, value in (getattr(im, "info", None) or {}).items():
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "ignore")
                if isinstance(value, str):
                    parts.append(f"{key}: {value}")
            return "\n".join(parts)
    except Exception:
        return ""


def _looks_like_text_only_error_screen(image_bytes: bytes) -> bool:
    try:
        from PIL import Image, ImageStat

        with Image.open(BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            width, height = im.size
            if width < 300 or height < 200:
                return False
            small = im.resize((160, max(1, int(160 * height / width))))
            gray = small.convert("L")
            stat = ImageStat.Stat(gray)
            mean = stat.mean[0]
            if mean < 205:
                return False
            pixels = list(gray.getdata())
            total = len(pixels)
            dark = sum(1 for p in pixels if p < 90) / total
            light = sum(1 for p in pixels if p > 235) / total
            # Provider policy screens are usually mostly blank/light with compact dark text.
            return light > 0.55 and 0.003 <= dark <= 0.22
    except Exception:
        return False


def detect_provider_error_screen(image_bytes: bytes) -> DetectionResult:
    """Detect provider-rendered moderation/error raster artifacts.

    This intentionally requires provider-policy text evidence where available, or a
    conservative text-only screen shape. It does not flag ordinary scene images just
    because they contain mirrors, bathrooms, reflections, or incidental text.
    """
    text = _metadata_text(image_bytes)
    for pattern, reason in _PROVIDER_ERROR_PATTERNS:
        if pattern.search(text):
            return DetectionResult(True, reason, "high")
    if text and any(word in text.lower() for word in ("venice", "moderation", "terms of service", "support@")):
        return DetectionResult(True, "provider_branded_policy_error_metadata", "medium")
    if _looks_like_text_only_error_screen(image_bytes):
        return DetectionResult(True, "text_only_error_screen", "medium")
    return DetectionResult(False, "", "low")
