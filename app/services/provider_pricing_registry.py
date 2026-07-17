from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

REGISTRY_VERSION = "venice-2026-coin-billing-v1"
EFFECTIVE_DATE = "2026-07-12"

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

    def snapshot(self) -> dict:
        data = asdict(self)
        data["standard_rate_usd"] = str(self.standard_rate_usd)
        data["long_context_rate_usd"] = str(self.long_context_rate_usd) if self.long_context_rate_usd is not None else None
        return data

_ENTRIES = {
    ("venice","qwen-3-6-plus","chat_input"): ProviderPrice("venice","qwen-3-6-plus","chat_input","1m_tokens",Decimal("0.63"),Decimal("2.50")),
    ("venice","qwen-3-6-plus","chat_output"): ProviderPrice("venice","qwen-3-6-plus","chat_output","1m_tokens",Decimal("3.75"),Decimal("7.50")),
    ("venice","qwen3-vl-235b-a22b","vision_input"): ProviderPrice("venice","qwen3-vl-235b-a22b","vision_input","1m_tokens",Decimal("0.21")),
    ("venice","qwen3-vl-235b-a22b","vision_output"): ProviderPrice("venice","qwen3-vl-235b-a22b","vision_output","1m_tokens",Decimal("1.90")),
    ("venice","e2ee-qwen3-vl-30b-a3b-p","vision_input"): ProviderPrice("venice","e2ee-qwen3-vl-30b-a3b-p","vision_input","1m_tokens",Decimal("0.25")),
    ("venice","e2ee-qwen3-vl-30b-a3b-p","vision_output"): ProviderPrice("venice","e2ee-qwen3-vl-30b-a3b-p","vision_output","1m_tokens",Decimal("0.90")),
    ("venice","openai/whisper-large-v3","stt"): ProviderPrice("venice","openai/whisper-large-v3","stt","audio_second",Decimal("0.0001")),
    ("venice","nvidia/parakeet-tdt-0.6b-v3","stt"): ProviderPrice("venice","nvidia/parakeet-tdt-0.6b-v3","stt","audio_second",Decimal("0.0001")),
    ("venice","fal-ai/wizper","stt"): ProviderPrice("venice","fal-ai/wizper","stt","audio_second",Decimal("0.0001")),
    ("venice","elevenlabs/scribe-v2","stt"): ProviderPrice("venice","elevenlabs/scribe-v2","stt","audio_second",Decimal("0.0002")),
    ("venice","tts-gemini-3-1-flash","tts"): ProviderPrice("venice","tts-gemini-3-1-flash","tts","character",Decimal("0.0001875")),
    ("venice","krea-2-turbo","image_1k"): ProviderPrice("venice","krea-2-turbo","image_1k","image",Decimal("0.04")),
    ("venice","krea-2-turbo","image_2k"): ProviderPrice("venice","krea-2-turbo","image_2k","image",Decimal("0.06")),
    ("venice","seedream-v5-lite","image_1k"): ProviderPrice("venice","seedream-v5-lite","image_1k","image",Decimal("0.05")),
    ("venice","seedream-v5-lite","image_2k"): ProviderPrice("venice","seedream-v5-lite","image_2k","image",Decimal("0.05")),
}
PRICING_REGISTRY: Mapping[tuple[str,str,str], ProviderPrice] = MappingProxyType(_ENTRIES)

def get_price(provider: str, model_id: str, feature: str) -> ProviderPrice:
    return PRICING_REGISTRY[(provider, model_id, feature)]

def list_prices() -> list[ProviderPrice]:
    return list(PRICING_REGISTRY.values())
