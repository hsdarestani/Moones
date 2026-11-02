from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from datetime import date
from dataclasses import replace
from types import MappingProxyType
from typing import Mapping

REGISTRY_VERSION = "venice-2026-11-02-v3"
EFFECTIVE_DATE = "2026-11-02"


@dataclass(frozen=True)
class ProviderPrice:
    provider: str
    model_id: str
    feature: str
    billing_unit: str
    standard_rate_usd: Decimal
    long_context_rate_usd: Decimal | None = None
    effective_date: str = EFFECTIVE_DATE
    source_label: str = "Venice official pricing"
    registry_version: str = REGISTRY_VERSION
    cache_read_rate_usd: Decimal | None = None
    cache_write_rate_usd: Decimal | None = None

    def snapshot(self) -> dict:
        data = asdict(self)
        data["standard_rate_usd"] = str(self.standard_rate_usd)
        data["long_context_rate_usd"] = (
            str(self.long_context_rate_usd)
            if self.long_context_rate_usd is not None
            else None
        )
        for key in ("cache_read_rate_usd", "cache_write_rate_usd"):
            data[key] = str(data[key]) if data[key] is not None else None
        return data


_ENTRIES = {
    ("google", "gemini-3.8-flash-lite-tts", "tts_input"): ProviderPrice("google", "gemini-3.8-flash-lite-tts", "tts_input", "1m_tokens", Decimal("0.50"), source_label="Google official pricing; promotional until 2026-12-31"),
    ("google", "gemini-3.8-flash-lite-tts", "tts_output"): ProviderPrice("google", "gemini-3.8-flash-lite-tts", "tts_output", "1m_audio_tokens", Decimal("6.00"), source_label="Google official pricing; promotional until 2026-12-31"),
    ("venice", "qwen-3-6-plus", "chat_input"): ProviderPrice("venice", "qwen-3-6-plus", "chat_input", "1m_tokens", Decimal("0.625"), Decimal("2.50"), cache_read_rate_usd=Decimal("0.0625"), cache_write_rate_usd=Decimal("0.78")),
    ("venice", "qwen-3-6-plus", "chat_output"): ProviderPrice("venice", "qwen-3-6-plus", "chat_output", "1m_tokens", Decimal("3.75"), Decimal("7.50")),
    ("venice", "qwen3-235b-a22b-instruct-2507", "chat_input"): ProviderPrice("venice", "qwen3-235b-a22b-instruct-2507", "chat_input", "1m_tokens", Decimal("0.15")),
    ("venice", "qwen3-235b-a22b-instruct-2507", "chat_output"): ProviderPrice("venice", "qwen3-235b-a22b-instruct-2507", "chat_output", "1m_tokens", Decimal("0.75")),
    ("venice", "qwen-3-8-flash", "chat_input"): ProviderPrice("venice", "qwen-3-8-flash", "chat_input", "1m_tokens", Decimal("0.14"), cache_read_rate_usd=Decimal("0.014")),
    ("venice", "qwen-3-8-flash", "chat_output"): ProviderPrice("venice", "qwen-3-8-flash", "chat_output", "1m_tokens", Decimal("0.49")),
    ("venice", "tts-gemini-3-1-flash", "tts"): ProviderPrice("venice", "tts-gemini-3-1-flash", "tts", "character", Decimal("0.0001875")),
    ("venice", "qwen3-vl-235b-a22b", "vision_input"): ProviderPrice("venice", "qwen3-vl-235b-a22b", "vision_input", "1m_tokens", Decimal("0.21")),
    ("venice", "qwen3-vl-235b-a22b", "vision_output"): ProviderPrice("venice", "qwen3-vl-235b-a22b", "vision_output", "1m_tokens", Decimal("1.90")),
    ("venice", "e2ee-qwen3-vl-30b-a3b-p", "vision_input"): ProviderPrice("venice", "e2ee-qwen3-vl-30b-a3b-p", "vision_input", "1m_tokens", Decimal("0.25")),
    ("venice", "e2ee-qwen3-vl-30b-a3b-p", "vision_output"): ProviderPrice("venice", "e2ee-qwen3-vl-30b-a3b-p", "vision_output", "1m_tokens", Decimal("0.90")),
    ("venice", "openai/whisper-large-v3", "stt"): ProviderPrice("venice", "openai/whisper-large-v3", "stt", "audio_second", Decimal("0.0001")),
    ("venice", "nvidia/parakeet-tdt-0.6b-v3", "stt"): ProviderPrice("venice", "nvidia/parakeet-tdt-0.6b-v3", "stt", "audio_second", Decimal("0.0001")),
    ("venice", "fal-ai/wizper", "stt"): ProviderPrice("venice", "fal-ai/wizper", "stt", "audio_second", Decimal("0.0001")),
    ("venice", "elevenlabs/scribe-v2", "stt"): ProviderPrice("venice", "elevenlabs/scribe-v2", "stt", "audio_second", Decimal("0.0002")),
    # Kept for settling old queued jobs created before the model was retired.
    ("venice", "krea-2-turbo", "image_1k"): ProviderPrice("venice", "krea-2-turbo", "image_1k", "image", Decimal("0.04")),
    ("venice", "krea-2-turbo", "image_2k"): ProviderPrice("venice", "krea-2-turbo", "image_2k", "image", Decimal("0.06")),
    ("venice", "seedream-v5-lite", "image_1k"): ProviderPrice("venice", "seedream-v5-lite", "image_1k", "image", Decimal("0.05")),
    ("venice", "seedream-v5-lite", "image_2k"): ProviderPrice("venice", "seedream-v5-lite", "image_2k", "image", Decimal("0.05")),
    ("venice", "seedream-v5-lite-edit", "image_1k"): ProviderPrice("venice", "seedream-v5-lite-edit", "image_1k", "image", Decimal("0.05")),
    ("venice", "seedream-v5-lite-edit", "image_2k"): ProviderPrice("venice", "seedream-v5-lite-edit", "image_2k", "image", Decimal("0.05")),
    ("venice", "venice-sd35", "image_1k"): ProviderPrice("venice", "venice-sd35", "image_1k", "image", Decimal("0.01")),
    ("venice", "venice-sd35", "image_2k"): ProviderPrice("venice", "venice-sd35", "image_2k", "image", Decimal("0.01")),
    ("venice", "z-image-turbo", "image_1k"): ProviderPrice("venice", "z-image-turbo", "image_1k", "image", Decimal("0.01")),
    ("venice", "z-image-turbo", "image_2k"): ProviderPrice("venice", "z-image-turbo", "image_2k", "image", Decimal("0.01")),
    ("venice", "lustify-sdxl", "image_1k"): ProviderPrice("venice", "lustify-sdxl", "image_1k", "image", Decimal("0.01")),
    ("venice", "lustify-sdxl", "image_2k"): ProviderPrice("venice", "lustify-sdxl", "image_2k", "image", Decimal("0.01")),
    ("venice", "lustify-v8", "image_1k"): ProviderPrice("venice", "lustify-v8", "image_1k", "image", Decimal("0.01")),
    ("venice", "lustify-v8", "image_2k"): ProviderPrice("venice", "lustify-v8", "image_2k", "image", Decimal("0.01")),
}

PRICING_REGISTRY: Mapping[tuple[str, str, str], ProviderPrice] = MappingProxyType(_ENTRIES)


def get_price(provider: str, model_id: str, feature: str) -> ProviderPrice:
    price = PRICING_REGISTRY[(provider, model_id, feature)]
    if provider == "google" and model_id == "gemini-3.8-flash-lite-tts" and date.today() >= date(2027, 1, 1):
        return replace(price, standard_rate_usd=price.standard_rate_usd * 2,
                       effective_date="2027-01-01", source_label="Google official standard pricing")
    return price


def list_prices() -> list[ProviderPrice]:
    return list(PRICING_REGISTRY.values())
