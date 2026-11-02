"""Explicitly configured direct Google speech. No provider fallback or retries.

REST contract: https://ai.google.dev/gemini-api/docs/speech-generation
"""
from __future__ import annotations

import base64
import httpx


class SpeechBytes(bytes):
    def __new__(cls, data: bytes, *, provider: str, model: str, input_tokens=None, output_tokens=None):
        obj = super().__new__(cls, data)
        obj.provider, obj.model = provider, model
        obj.input_tokens, obj.output_tokens = input_tokens, output_tokens
        return obj


async def google_speech(text: str, *, settings, voice: str, mood: str | None = None) -> SpeechBytes:
    from app.llm.tts_client import TTSFailure, _convert_to_ogg_opus
    if not settings.gemini_api_key:
        raise TTSFailure("google_key_missing")
    if settings.google_tts_model != "gemini-3.8-flash-lite-tts":
        raise TTSFailure("google_tts_model_not_priced")
    style = "Natural conversational Iranian Persian; warm, casual, clear."
    if mood in {"sad", "calm", "serious"}:
        style += " Calm and gentle delivery."
    payload = {
        "model": settings.google_tts_model, "store": False, "stream": False,
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
            "annotations": [{"type": "speech_metadata", "style": style}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}],
            "max_output_tokens": max(250, min(3000, settings.tts_max_audio_seconds * 25))},
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}, json=payload)
        if response.status_code >= 400:
            raise TTSFailure(f"google_http_{response.status_code}")
        data = response.json()
        if data.get("status") not in {None, "completed"}:
            raise TTSFailure("google_audio_incomplete")
        audio_blocks = [block for step in data.get("steps", []) if step.get("type") == "model_output"
                        for block in step.get("content", []) if block.get("type") == "audio"]
        if not audio_blocks:
            raise TTSFailure("google_audio_missing")
        block = audio_blocks[-1]
        raw = base64.b64decode(block.get("data", ""), validate=True)
        if not raw or len(raw) > 12_000_000:
            raise TTSFailure("google_audio_size_invalid")
        mime = block.get("mime_type", "audio/wav")
        if mime not in {"audio/wav", "audio/x-wav", "audio/ogg"}:
            raise TTSFailure("google_audio_format_unsupported")
        audio = raw if mime == "audio/ogg" else await _convert_to_ogg_opus(raw, ".wav")
        usage = data.get("usage") or {}
        return SpeechBytes(audio, provider="google", model=settings.google_tts_model,
            input_tokens=usage.get("total_input_tokens"), output_tokens=usage.get("total_output_tokens"))
    except TTSFailure:
        raise
    except Exception as exc:
        # Never put upstream response text or API key URLs in logs/errors.
        raise TTSFailure(f"google_speech_{type(exc).__name__}") from exc
