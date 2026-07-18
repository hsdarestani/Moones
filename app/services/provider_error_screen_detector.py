from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
import re

from PIL import Image, ImageFilter, ImageStat


@dataclass(frozen=True)
class DetectionResult:
    is_error_screen: bool
    reason: str = ""
    confidence: str = "low"
    diagnostics: dict[str, float | int] = field(default_factory=dict)


_PROVIDER_ERROR_PATTERNS = (
    (re.compile(r"systems\s+have\s+detected\s+content.*violates\s+our\s+terms\s+of\s+service", re.I | re.S), "venice_terms_moderation_text"),
    (re.compile(r"please\s+try\s+changing\s+your\s+prompt.*trying\s+another\s+model", re.I | re.S), "venice_prompt_or_model_error_text"),
    (re.compile(r"contact\s+support@venice\.ai", re.I), "venice_support_policy_text"),
    (re.compile(r"moderation|policy\s+violation|violates\s+(our\s+)?terms\s+of\s+service|content\s+policy", re.I), "provider_policy_error_text"),
)


def _metadata_text(image_bytes: bytes) -> str:
    try:
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


def _corner_ratio(mask: list[bool], width: int, height: int) -> float:
    box_w = max(1, width // 5)
    box_h = max(1, height // 5)
    hits = total = 0
    for y0 in (0, height - box_h):
        for x0 in (0, width - box_w):
            for y in range(y0, y0 + box_h):
                row = y * width
                for x in range(x0, x0 + box_w):
                    total += 1
                    hits += int(mask[row + x])
    return hits / max(1, total)


def _bands(row_counts: list[int], width: int) -> list[tuple[int, int]]:
    min_density = 0.012
    bands: list[tuple[int, int]] = []
    start: int | None = None
    quiet = 0
    for y, count in enumerate(row_counts):
        active = count / max(1, width) >= min_density
        if active and start is None:
            start = y
            quiet = 0
        elif active:
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= 2:
                end = y - quiet
                if end >= start:
                    bands.append((start, end))
                start = None
                quiet = 0
    if start is not None:
        bands.append((start, len(row_counts) - 1))
    return bands


def _round_metric(value: float) -> float:
    return round(float(value), 4)


def _summarize_diagnostics(metrics: dict[str, float | int]) -> dict[str, float | int]:
    keys = (
        "near_black_ratio",
        "near_white_ratio",
        "foreground_ratio",
        "grayscale_ratio",
        "edge_ratio",
        "contrast",
        "corner_ratio",
        "band_count",
        "margin_x",
        "margin_y",
        "box_width_ratio",
        "box_height_ratio",
        "center_x",
        "center_y",
    )
    return {key: metrics[key] for key in keys if key in metrics}


def _looks_like_text_card(image_bytes: bytes) -> DetectionResult:
    diagnostics: dict[str, float | int] = {}
    try:
        with Image.open(BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            width, height = im.size
            if width < 300 or height < 200:
                return DetectionResult(False, diagnostics=diagnostics)
            sample_h = max(1, int(256 * height / width))
            small = im.resize((256, sample_h))
            width, height = small.size
            gray = small.convert("L")
            pixels = list(gray.getdata())
            rgb_pixels = list(small.getdata())
            total = len(pixels)

            near_black = [p < 32 for p in pixels]
            near_white = [p > 235 for p in pixels]
            dark_fg = [p < 135 for p in pixels]
            light_fg = [p > 100 for p in pixels]
            near_black_ratio = sum(near_black) / total
            near_white_ratio = sum(near_white) / total
            dark_foreground_ratio = sum(dark_fg) / total
            light_foreground_ratio = sum(light_fg) / total
            grayscale_ratio = sum(1 for r, g, b in rgb_pixels if max(r, g, b) - min(r, g, b) <= 18) / total

            # Broad photographic texture produces many local edges, while rendered text
            # cards have sparse edges localized to glyph rows.
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_ratio = sum(1 for p in edges.getdata() if p > 35) / total
            contrast = ImageStat.Stat(gray).stddev[0]

            def analyze_foreground(foreground_mask: list[bool], background_mask: list[bool], foreground_ratio: float) -> dict[str, float | int]:
                xs: list[int] = []
                ys: list[int] = []
                row_counts = [0 for _ in range(height)]
                for idx, is_fg in enumerate(foreground_mask):
                    if not is_fg:
                        continue
                    y, x = divmod(idx, width)
                    xs.append(x)
                    ys.append(y)
                    row_counts[y] += 1
                corner_ratio = _corner_ratio(background_mask, width, height)
                metrics: dict[str, float | int] = {
                    "near_black_ratio": _round_metric(near_black_ratio),
                    "near_white_ratio": _round_metric(near_white_ratio),
                    "foreground_ratio": _round_metric(foreground_ratio),
                    "grayscale_ratio": _round_metric(grayscale_ratio),
                    "edge_ratio": _round_metric(edge_ratio),
                    "contrast": _round_metric(contrast),
                    "corner_ratio": _round_metric(corner_ratio),
                    "band_count": 0,
                }
                if not xs:
                    return metrics
                min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
                box_w = max_x - min_x + 1
                box_h = max_y - min_y + 1
                bands = _bands(row_counts, width)
                separated = [b for b in bands if 1 <= (b[1] - b[0] + 1) <= max(12, height // 8)]
                metrics.update({
                    "band_count": len(separated),
                    "margin_x": _round_metric(min(min_x, width - 1 - max_x) / width),
                    "margin_y": _round_metric(min(min_y, height - 1 - max_y) / height),
                    "box_width_ratio": _round_metric(box_w / width),
                    "box_height_ratio": _round_metric(box_h / height),
                    "center_x": _round_metric((min_x + max_x) / 2 / width),
                    "center_y": _round_metric((min_y + max_y) / 2 / height),
                })
                return metrics

            dark_metrics = analyze_foreground(light_fg, near_black, light_foreground_ratio)
            diagnostics = _summarize_diagnostics(dark_metrics)

            if (
                near_black_ratio >= 0.85
                and dark_metrics.get("corner_ratio", 0) >= 0.97
                and grayscale_ratio >= 0.95
                and 0.01 <= light_foreground_ratio <= 0.20
                and 0.005 <= edge_ratio <= 0.12
                and contrast >= 12
                and dark_metrics.get("band_count", 0) >= 4
                and 0.35 <= dark_metrics.get("center_x", -1) <= 0.65
                and 0.20 <= dark_metrics.get("center_y", -1) <= 0.80
                and dark_metrics.get("margin_x", -1) >= 0.04
                and dark_metrics.get("box_width_ratio", 999) <= 0.92
                and dark_metrics.get("box_height_ratio", 999) <= 0.65
            ):
                return DetectionResult(True, "dark_provider_moderation_card", "high", diagnostics)

            def check(*, background_mask: list[bool], foreground_mask: list[bool], bg_ratio: float, fg_ratio: float, bg_corner_min: float, reason: str) -> DetectionResult:
                metrics = analyze_foreground(foreground_mask, background_mask, fg_ratio)
                summarized = _summarize_diagnostics(metrics)
                if bg_ratio < bg_corner_min or not (0.003 <= fg_ratio <= 0.25):
                    return DetectionResult(False, diagnostics=summarized)
                if grayscale_ratio < 0.86 or contrast < 12 or edge_ratio > 0.22:
                    return DetectionResult(False, diagnostics=summarized)
                if metrics.get("corner_ratio", 0) < 0.88:
                    return DetectionResult(False, diagnostics=summarized)
                if not metrics.get("box_width_ratio"):
                    return DetectionResult(False, diagnostics=summarized)
                if (
                    metrics.get("margin_x", 0) < 0.12
                    or metrics.get("margin_y", 0) < 0.12
                    or not (0.30 <= metrics.get("center_x", -1) <= 0.70 and 0.25 <= metrics.get("center_y", -1) <= 0.75)
                ):
                    return DetectionResult(False, diagnostics=summarized)
                if metrics["box_width_ratio"] < 0.22 or metrics["box_width_ratio"] > 0.82 or metrics.get("box_height_ratio", 999) > 0.55:
                    return DetectionResult(False, diagnostics=summarized)
                if metrics.get("band_count", 0) < 3:
                    return DetectionResult(False, diagnostics=summarized)
                return DetectionResult(True, reason, "medium", summarized)

            dark_result = check(
                background_mask=near_black,
                foreground_mask=light_fg,
                bg_ratio=near_black_ratio,
                fg_ratio=light_foreground_ratio,
                bg_corner_min=0.70,
                reason="dark_text_only_provider_error_screen",
            )
            if dark_result.is_error_screen:
                return dark_result
            light_result = check(
                background_mask=near_white,
                foreground_mask=dark_fg,
                bg_ratio=near_white_ratio,
                fg_ratio=dark_foreground_ratio,
                bg_corner_min=0.55,
                reason="light_text_only_provider_error_screen",
            )
            if light_result.is_error_screen:
                return light_result
            return DetectionResult(False, diagnostics=diagnostics)
    except Exception:
        return DetectionResult(False, diagnostics=diagnostics)
    return DetectionResult(False, diagnostics=diagnostics)

def detect_provider_error_screen(image_bytes: bytes) -> DetectionResult:
    """Detect provider-rendered moderation/error raster artifacts."""
    text = _metadata_text(image_bytes)
    for pattern, reason in _PROVIDER_ERROR_PATTERNS:
        if pattern.search(text):
            return DetectionResult(True, reason, "high")
    if text and any(word in text.lower() for word in ("venice", "moderation", "terms of service", "support@")):
        return DetectionResult(True, "provider_branded_policy_error_metadata", "medium")
    return _looks_like_text_card(image_bytes)
