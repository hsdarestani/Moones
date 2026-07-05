from __future__ import annotations
import httpx
from app.core.config import get_settings

async def transcribe_audio_with_venice(audio_path: str, *, model: str | None = None, language: str = "fa") -> dict:
    settings=get_settings(); model=model or settings.stt_model
    with open(audio_path,'rb') as fh:
        files={"file": (audio_path.rsplit('/',1)[-1], fh, "audio/ogg")}
        data={"model": model, "language": language}
        async with httpx.AsyncClient(timeout=60) as client:
            r=await client.post(f"{settings.venice_api_base_url.rstrip('/')}/audio/transcriptions", headers={"Authorization":f"Bearer {settings.venice_api_key}"}, data=data, files=files)
    if r.status_code>=400: raise RuntimeError(r.text[:500])
    js=r.json(); return {"text": (js.get("text") or "").strip(), "duration_seconds": js.get("duration"), "model": model}
