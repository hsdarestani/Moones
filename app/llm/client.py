from dataclasses import dataclass
import logging
import os
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

FALLBACK_LLM_TEXT = "یه لحظه قاطی کردم، دوباره بگو عزیزم."
EXTRACTION_PATHS_TRIED = [
    "choices[0].message.content",
    "choices[0].text",
    "choices[0].content",
    "output_text",
    "content",
    "message.content",
    "data.text",
    "data.content",
]


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
    raw_response_text: str | None = None
    parsed_response: dict | None = None
    extraction_path: str | None = None
    extraction_error: str | None = None
    retry_used: bool = False


def extract_text_from_venice_response(data: dict[str, Any]) -> tuple[str, str]:
    def content_to_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                    elif isinstance(text, list):
                        nested = content_to_text(text)
                        if nested:
                            parts.append(nested)
            return " ".join(part.strip() for part in parts if part and part.strip()).strip()
        if isinstance(value, dict):
            for key in ("text", "content"):
                text = content_to_text(value.get(key))
                if text:
                    return text
        return ""

    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                text = content_to_text(message.get("content"))
                if text:
                    return text, "choices[0].message.content"
            for key, path in (("text", "choices[0].text"), ("content", "choices[0].content")):
                text = content_to_text(first.get(key))
                if text:
                    return text, path
    for path, value in (
        ("output_text", data.get("output_text")),
        ("content", data.get("content")),
        ("message.content", (data.get("message") or {}).get("content") if isinstance(data.get("message"), dict) else None),
        ("data.text", (data.get("data") or {}).get("text") if isinstance(data.get("data"), dict) else None),
        ("data.content", (data.get("data") or {}).get("content") if isinstance(data.get("data"), dict) else None),
    ):
        text = content_to_text(value)
        if text:
            return text, path
    return "", "not_found"



def has_reasoning_content(data: dict[str, Any]) -> bool:
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            for source in (message, choice):
                value = source.get("reasoning_content") or source.get("reasoning")
                if isinstance(value, str) and value.strip():
                    return True
    return False

def _empty_extraction_error(data: dict[str, Any]) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    choices_len = len(choices) if isinstance(choices, list) else 0
    keys = sorted(data.keys()) if isinstance(data, dict) else []
    return f"empty_response top_level_keys={keys} choices_len={choices_len} paths_tried={EXTRACTION_PATHS_TRIED}"


class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.venice_api_key
        self.base_url = settings.venice_api_base_url.rstrip("/")
        self.model = settings.venice_model
        self.timeout = min(settings.venice_timeout_seconds, 9)
        self.debug = bool(getattr(settings, "llm_debug", False)) or os.getenv("LLM_DEBUG", "").lower() in {"1", "true", "yes", "on"}

    async def complete_result(self, messages: list[dict[str, str]], model: str | None = None, parameters: dict | None = None, timeout: int | float | None = None, _retrying: bool = False) -> LLMResult:
        model = model or self.model
        if not self.api_key:
            return LLMResult(text="", model=model, error="VENICE_API_KEY missing")
        if model == "qwen-3-6-plus":
            default_parameters = {
                "temperature": 0.75,
                "top_p": 0.9,
                "frequency_penalty": 0.3,
                "presence_penalty": 0.2,
                "max_tokens": 350,
                "venice_parameters": {
                    "disable_thinking": True,
                    "strip_thinking_response": True,
                    "include_venice_system_prompt": False,
                    "enable_web_search": "off",
                },
            }
        else:
            default_parameters = {"temperature": 0.72, "top_p": 0.88, "frequency_penalty": 0.9, "presence_penalty": 0.35, "max_tokens": 120}
        default_parameters.update(parameters or {})
        if model == "qwen-3-6-plus":
            venice_params = dict(default_parameters.get("venice_parameters") or {})
            venice_params.update({
                "disable_thinking": True,
                "strip_thinking_response": True,
                "include_venice_system_prompt": False,
                "enable_web_search": "off",
            })
            default_parameters["venice_parameters"] = venice_params
        payload = {"model": model, "messages": messages, **default_parameters}
        vp = payload.get("venice_parameters") or {}
        logger.info(
            "VENICE_PARAMS model=%s disable_thinking=%s strip_thinking_response=%s max_tokens=%s",
            model, vp.get("disable_thinking"), vp.get("strip_thinking_response"), payload.get("max_tokens"),
        )
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        safe_payload = {"model": payload.get("model"), "message_count": len(payload.get("messages") or []), "max_tokens": payload.get("max_tokens"), "venice_parameters": payload.get("venice_parameters")}
        try:
            async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
                response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            status = response.status_code
            raw_text = response.text
            if self.debug:
                logger.info("VENICE_RAW status=%s body=%s", status, raw_text[:5000])
            minimal_headers = {k: response.headers.get(k) for k in ("content-type", "x-request-id", "retry-after") if response.headers.get(k)}
            try:
                data = response.json()
            except Exception as exc:
                err = f"invalid_json: {exc}; raw_len={len(raw_text)}"
                logger.warning("VENICE_EXTRACT model=%s status=%s headers=%s payload=%s error=%s", model, status, minimal_headers, safe_payload, err)
                return LLMResult("", model, status_code=status, error=err, raw_response_text=raw_text, extraction_error=err)
            usage = data.get("usage") or {}
            if status >= 400:
                err = str(data)[:1000]
                return LLMResult("", model, status_code=status, raw_usage=usage, error=err, raw_response_text=raw_text, parsed_response=data, extraction_error=err)
            text, path = extract_text_from_venice_response(data)
            if not text and has_reasoning_content(data) and not _retrying:
                retry_messages = list(messages) + [{"role": "system", "content": "Answer with final message only. No reasoning."}]
                retry_result = await self.complete_result(retry_messages, model=model, parameters=parameters, timeout=timeout, _retrying=True)
                retry_result.retry_used = True
                return retry_result
            extraction_error = None if text else _empty_extraction_error(data)
            logger.info(
                "VENICE_EXTRACT model=%s status=%s headers=%s extraction_path=%s extracted_text_length=%s extraction_error=%s payload=%s",
                model, status, minimal_headers, path, len(text), extraction_error, safe_payload,
            )
            return LLMResult(text=text, model=data.get("model") or model, input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"), raw_usage=usage, status_code=status, error=extraction_error, raw_response_text=raw_text, parsed_response=data, extraction_path=path, extraction_error=extraction_error, retry_used=_retrying)
        except httpx.TimeoutException:
            return LLMResult("", model, error="timeout")
        except Exception as exc:
            return LLMResult("", model, error=f"request_error: {exc}")

    async def complete(self, messages: list[dict[str, str]]) -> str:
        return (await self.complete_result(messages)).text or FALLBACK_LLM_TEXT
