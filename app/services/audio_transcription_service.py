from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class STTNotConfigured(RuntimeError):
    pass


class AudioTranscriptionService:
    def __init__(self) -> None:
        self.provider = (os.getenv("STT_PROVIDER") or "").strip().lower()
        self.api_key = os.getenv("STT_API_KEY") or ""
        self.base_url = (os.getenv("STT_BASE_URL") or "").rstrip("/")
        self.model = os.getenv("STT_MODEL") or "whisper-1"

    def _configured(self) -> bool:
        return bool(self.provider and self.api_key and self.base_url)

    def _maybe_convert(self, file_path: str) -> str:
        # OpenAI-compatible transcription endpoints usually accept ogg/opus. Convert only when explicitly requested.
        if (os.getenv("STT_FORCE_FORMAT") or "").lower() not in {"mp3", "wav"}:
            return file_path
        target_format = os.getenv("STT_FORCE_FORMAT", "mp3").lower()
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("VOICE_CONVERSION_SKIPPED reason=ffmpeg_unavailable")
            return file_path
        src = Path(file_path)
        dst = str(src.with_suffix(f".{target_format}"))
        subprocess.run([ffmpeg, "-y", "-i", file_path, dst], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return dst

    async def transcribe_telegram_voice(self, file_path: str, user_id: int | None = None, telegram_id: str | None = None, duration: int | None = None) -> str:
        if not self._configured():
            logger.info("VOICE_STT_NOT_CONFIGURED user_id=%s", user_id)
            raise STTNotConfigured("stt_not_configured")
        upload_path = self._maybe_convert(file_path)
        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with open(upload_path, "rb") as fh:
            files = {"file": (Path(upload_path).name, fh, "application/octet-stream")}
            data = {"model": self.model}
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        payload = response.json()
        text = (payload.get("text") or payload.get("transcript") or "").strip()
        if not text:
            raise RuntimeError("empty_transcript")
        logger.info("VOICE_TRANSCRIBED user_id=%s chars=%s provider=%s", user_id, len(text), self.provider)
        return text
