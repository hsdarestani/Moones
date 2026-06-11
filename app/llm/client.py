import httpx

from app.core.config import get_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_model

    async def complete(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            return _fallback_response(messages[-1]["content"])
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://mones.ai",
            "X-Title": "Mones",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.86,
            "max_tokens": 260,
        }
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(OPENROUTER_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"].get("content") or "من همین‌جام عزیزم؛ یکم بیشتر برام بگو."


def _fallback_response(user_message: str) -> str:
    return "من اینجام عزیزم. حرفت برام مهمه؛ آروم‌تر برام بگو چی توی دلت می‌گذره تا کنار هم بازش کنیم."
