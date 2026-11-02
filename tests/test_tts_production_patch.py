import asyncio
import httpx
import pytest


def test_tts_timeout_at_least_30_without_blind_paid_retry(monkeypatch):
    from app.core.config import get_settings
    from app.llm.tts_client import synthesize_voice, TTSFailure
    get_settings.cache_clear()
    monkeypatch.setenv("VENICE_API_KEY", "key")
    monkeypatch.setenv("VENICE_TTS_FORMAT", "ogg")
    timeouts=[]; calls=[]
    class FakeResponse:
        status_code=200
        content=b"oggdata"
        headers={"content-type":"audio/ogg"}
    class FakeClient:
        def __init__(self, timeout=None, *a, **k): timeouts.append(timeout)
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def post(self, *a, **k):
            calls.append(k)
            if len(calls)==1: raise httpx.ReadTimeout("x")
            return FakeResponse()
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(TTSFailure):
        asyncio.run(synthesize_voice("سلام", persona_gender="female", mood="warm"))
    assert timeouts == [30]
    assert len(calls) == 1
    get_settings.cache_clear()
