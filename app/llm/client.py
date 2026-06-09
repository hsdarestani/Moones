from openai import AsyncOpenAI

from app.core.config import get_settings


class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.openai_model
        self.client = AsyncOpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    async def complete(self, messages: list[dict[str, str]]) -> str:
        if self.client is None:
            return _fallback_response(messages[-1]["content"])
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.8,
            max_tokens=220,
        )
        return response.choices[0].message.content or "من اینجام عزیزم؛ بیشتر برام بگو."


def _fallback_response(user_message: str) -> str:
    if any(ch in user_message for ch in "اآبپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی"):
        return "من اینجام عزیزم. حرفت برام مهمه؛ یکم بیشتر از حست بهم بگو تا کنارت باشم."
    return "I'm here with you. Tell me a little more about how you feel, and I'll stay close."
