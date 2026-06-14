from dataclasses import dataclass
import httpx
from app.core.config import get_settings

FALLBACK_LLM_TEXT = "یه لحظه ذهنم قفل کرد 😅\nدوباره برام بفرست، می‌خوام درست جوابتو بدم."

@dataclass
class LLMResult:
    text: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_usage: dict | None = None
    status_code: int | None = None
    error: str | None = None
    provider: str = "venice"

class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.venice_api_key
        self.base_url = settings.venice_api_base_url.rstrip("/")
        self.model = settings.venice_model
        self.timeout = settings.venice_timeout_seconds

    async def complete_result(self, messages: list[dict[str, str]], model: str | None = None) -> LLMResult:
        model = model or self.model
        if not self.api_key:
            return LLMResult(text=FALLBACK_LLM_TEXT, model=model, error="VENICE_API_KEY missing")
        payload = {"model": model, "messages": messages, "temperature": 0.72, "top_p": 0.88, "frequency_penalty": 0.9, "presence_penalty": 0.35, "max_tokens": 220}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            status = response.status_code
            try:
                data = response.json()
            except Exception as exc:
                return LLMResult(FALLBACK_LLM_TEXT, model, status_code=status, error=f"invalid_json: {exc}")
            if status >= 400:
                return LLMResult(FALLBACK_LLM_TEXT, model, status_code=status, error=str(data)[:1000])
            usage = data.get("usage") or {}
            text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if not text:
                return LLMResult(FALLBACK_LLM_TEXT, model, status_code=status, raw_usage=usage, error="empty_response")
            return LLMResult(text=text, model=data.get("model") or model, input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"), raw_usage=usage, status_code=status)
        except httpx.TimeoutException:
            return LLMResult(FALLBACK_LLM_TEXT, model, error="timeout")
        except Exception as exc:
            return LLMResult(FALLBACK_LLM_TEXT, model, error=f"request_error: {exc}")

    async def complete(self, messages: list[dict[str, str]]) -> str:
        return (await self.complete_result(messages)).text
