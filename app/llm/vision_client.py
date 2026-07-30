from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.llm.client import extract_text_from_venice_response


VISION_PROMPT = '''You are a visual perception module for a Persian AI companion.
Analyze the image carefully and return JSON only.
Do not identify the person. Do not infer sensitive attributes such as exact age, ethnicity, religion, health, wealth, or exact location. Do not sexualize the person. If the image may contain a minor, keep compliments non-romantic and non-sexual. If the image is unclear, say confidence is low.
Return: {"image_type":"selfie | portrait | group | object | place | screenshot | unclear","has_person":true,"may_contain_minor":false,"visible_details":[],"mood":"","style":"","safe_compliment_angles":[],"things_to_ask_about":[],"caption_context":"","confidence":"low | medium | high"}'''


def _json(text: str) -> dict:
    value = str(text or '').strip()
    if not value:
        raise ValueError('empty_vision_response')
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", value, re.S)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError('vision_response_not_object')
    return parsed


def detect_image_mime(image_bytes: bytes, *, fallback: str = 'image/jpeg') -> str:
    data = bytes(image_bytes or b'')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    return fallback


def _vision_timeout_seconds(settings, explicit: float | None) -> float:
    value = explicit if explicit is not None else getattr(
        settings,
        'vision_request_timeout_seconds',
        45,
    )
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 45.0
    return min(120.0, max(10.0, value))


def _vision_payload(
    *,
    model: str,
    prompt: str,
    image_bytes: bytes,
    response_format: dict | bool | None,
) -> dict:
    mime_type = detect_image_mime(image_bytes)
    encoded = base64.b64encode(image_bytes).decode('ascii')
    payload = {
        'model': model,
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': f'data:{mime_type};base64,{encoded}',
                        },
                    },
                ],
            }
        ],
        'temperature': 0.0,
        'max_tokens': 900,
        'venice_parameters': {
            'include_venice_system_prompt': False,
            'disable_thinking': True,
            'strip_thinking_response': True,
        },
    }
    structured = {'type': 'json_object'} if response_format is None else response_format
    if structured is not False:
        payload['response_format'] = structured
    return payload


async def _post_vision_request(
    *,
    payload: dict,
    timeout_seconds: float,
    client: httpx.AsyncClient | None,
) -> httpx.Response:
    settings = get_settings()
    url = f"{settings.venice_api_base_url.rstrip('/')}/chat/completions"
    headers = {
        'Authorization': f'Bearer {settings.venice_api_key}',
        'Content-Type': 'application/json',
    }
    if client is not None:
        return await client.post(url, headers=headers, json=payload)
    timeout = httpx.Timeout(
        connect=min(10.0, timeout_seconds),
        read=timeout_seconds,
        write=min(30.0, timeout_seconds),
        pool=min(10.0, timeout_seconds),
    )
    async with httpx.AsyncClient(timeout=timeout) as owned_client:
        return await owned_client.post(url, headers=headers, json=payload)


async def analyze_image_with_venice(
    image_path: str,
    *,
    user_caption: str | None = None,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float | None = None,
    response_format: dict | bool | None = None,
) -> dict:
    prompt = VISION_PROMPT + (
        f'\nUser caption: {user_caption}' if user_caption else ''
    )
    return await analyze_image_bytes_with_venice(
        Path(image_path).read_bytes(),
        prompt=prompt,
        model=model,
        client=client,
        timeout_seconds=timeout_seconds,
        response_format=response_format,
    )


async def analyze_image_bytes_with_venice(
    image_bytes: bytes,
    *,
    prompt: str | None = None,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float | None = None,
    response_format: dict | bool | None = None,
) -> dict:
    settings = get_settings()
    selected_model = model or settings.vision_model
    timeout = _vision_timeout_seconds(settings, timeout_seconds)
    payload = _vision_payload(
        model=selected_model,
        prompt=prompt or VISION_PROMPT,
        image_bytes=image_bytes,
        response_format=response_format,
    )
    response = await _post_vision_request(
        payload=payload,
        timeout_seconds=timeout,
        client=client,
    )
    if response.status_code >= 400:
        detail = ' '.join((response.text or '').split())[:500]
        raise RuntimeError(f'vision_http_{response.status_code}:{detail}')
    text, extraction_error = extract_text_from_venice_response(response.json())
    if extraction_error and not text:
        raise RuntimeError(f'vision_extract_failed:{extraction_error}')
    output = _json(text)
    output['model'] = selected_model
    return output
