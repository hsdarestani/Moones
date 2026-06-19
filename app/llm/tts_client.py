from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

class TTSFailure(Exception):
    pass

async def _convert_to_ogg_opus(data: bytes, suffix: str) -> bytes:
    def run() -> bytes:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / f"in{suffix}"
            dst = Path(td) / "out.ogg"
            src.write_bytes(data)
            subprocess.run(["ffmpeg", "-y", "-i", str(src), "-c:a", "libopus", "-f", "ogg", str(dst)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            return dst.read_bytes()
    return await asyncio.to_thread(run)

def select_gemini_voice(persona_gender: str | None = None, mood: str | None = None) -> str:
    gender = (persona_gender or "female").lower()
    key = (mood or "default").lower()
    female = {"warm": "Sulafat", "playful": "Aoede", "teasing": "Aoede", "calm": "Sulafat", "default": "Sulafat"}
    male = {"friendly": "Puck", "upbeat": "Puck", "playful": "Puck", "calm": "Iapetus", "clear": "Iapetus", "default": "Puck"}
    return (male if gender in {"male", "مرد", "پسر"} else female).get(key, (male if gender in {"male", "مرد", "پسر"} else female)["default"])

async def synthesize_voice(text: str, voice: str | None = None, persona_gender: str | None = None, mood: str | None = None) -> bytes:
    settings = get_settings()
    started = time.perf_counter()
    selected_voice = voice or settings.venice_tts_voice or select_gemini_voice(persona_gender, mood)
    if not settings.venice_tts_enabled:
        logger.info("TTS_RESULT enabled=False model=%s voice=%s success=False duration_ms=0 error=disabled", settings.venice_tts_model, selected_voice or "")
        raise TTSFailure("disabled")
    if not settings.venice_api_key:
        logger.info("TTS_RESULT enabled=True model=%s voice=%s success=False duration_ms=0 error=missing_key", settings.venice_tts_model, selected_voice or "")
        raise TTSFailure("missing_key")
    payload = {"model": settings.venice_tts_model, "input": text, "response_format": settings.venice_tts_format}
    if selected_voice:
        payload["voice"] = selected_voice
    try:
        timeout = max(30, int(settings.venice_timeout_seconds or 0))
        response = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(f"{settings.venice_api_base_url.rstrip('/')}/audio/speech", headers={"Authorization": f"Bearer {settings.venice_api_key}"}, json=payload)
                break
            except httpx.ReadTimeout:
                if attempt == 1:
                    raise
                logger.info("TTS_RETRY model=%s voice=%s reason=ReadTimeout", settings.venice_tts_model, selected_voice or "")
        assert response is not None
        if response.status_code >= 400 or not response.content:
            raise TTSFailure(f"status_{response.status_code}")
        content_type = response.headers.get("content-type", "")
        audio = response.content
        if "ogg" not in content_type and settings.venice_tts_format != "ogg":
            audio = await _convert_to_ogg_opus(audio, ".mp3" if "mpeg" in content_type else ".audio")
        logger.info("TTS_RESULT enabled=True model=%s voice=%s success=True duration_ms=%s error=", settings.venice_tts_model, selected_voice or "", int((time.perf_counter()-started)*1000))
        return audio
    except Exception as exc:
        logger.info("TTS_RESULT enabled=True model=%s voice=%s success=False duration_ms=%s error=%s", settings.venice_tts_model, selected_voice or "", int((time.perf_counter()-started)*1000), type(exc).__name__)
        raise TTSFailure(str(exc)) from exc
