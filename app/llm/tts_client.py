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

def select_tts_voice(user=None, partner_profile: dict | None = None, current_mood: str | None = None, persona_style: str | None = None) -> str:
    settings = get_settings()
    partner_profile = partner_profile or {}
    gender = (partner_profile.get("gender") or getattr(user, "partner_gender", None) or "").lower()
    mood = (current_mood or getattr(user, "current_mood", None) or "").lower()
    style = (persona_style or partner_profile.get("personality_type") or getattr(user, "partner_personality_type", None) or "").lower()
    combined = f"{mood} {style}"
    female_values = {"female", "girl", "woman", "زن", "دختر"}
    male_values = {"male", "boy", "man", "مرد", "پسر"}
    playful_terms = ("playful", "teasing", "شیطون", "بازیگوش", "شوخ")
    calm_terms = ("calm", "serious", "warm", "آروم", "جدی", "مهربون", "caring")
    if gender in female_values:
        playful = any(x in combined for x in playful_terms)
        voice = settings.tts_female_playful_voice if playful else settings.tts_female_default_voice
        reason = "female_playful" if playful else "female_default"
    elif gender in male_values:
        playful = any(x in combined for x in playful_terms)
        calm = any(x in combined for x in calm_terms)
        if playful:
            voice = settings.tts_male_playful_voice; reason = "male_playful"
        elif calm:
            voice = settings.tts_male_calm_voice or settings.tts_male_default_voice; reason = "male_calm"
        else:
            voice = settings.tts_male_default_voice; reason = "male_default"
    else:
        voice = settings.tts_female_default_voice; reason = "unknown_gender_default"
    logger.info("TTS_VOICE_SELECTED user_id=%s partner_gender=%s mood=%s persona=%s voice=%s reason=%s", getattr(user, "id", None) or "-", gender or "unknown", mood or "default", style or "default", voice, reason)
    return voice

def select_gemini_voice(persona_gender: str | None = None, mood: str | None = None) -> str:
    return select_tts_voice(None, {"gender": persona_gender}, mood, None)

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
