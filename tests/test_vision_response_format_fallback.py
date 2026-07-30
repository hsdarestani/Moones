import asyncio
import json
from types import SimpleNamespace

import pytest

from app.llm import vision_client


PNG_BYTES = b"\x89PNG\r\n\x1a\nvision-fallback-test"


class FakeResponse:
    def __init__(self, status_code, *, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


class UnsupportedThenJSONClient:
    def __init__(self):
        self.payloads = []

    async def post(self, url, *, headers, json):
        self.payloads.append(json)
        if len(self.payloads) == 1:
            return FakeResponse(
                400,
                text=json_module.dumps(
                    {
                        "details": {
                            "_errors": [
                                "response_format is not supported by this model"
                            ]
                        }
                    }
                ),
            )
        return FakeResponse(
            200,
            payload={
                "choices": [
                    {
                        "message": {
                            "content": json_module.dumps(
                                {
                                    "anatomy_visible_enough_to_assess": True,
                                    "anatomy_consistent_with_profile": True,
                                    "confidence": "high",
                                    "reason_codes": [],
                                }
                            )
                        }
                    }
                ]
            },
        )


class OtherBadRequestClient:
    def __init__(self):
        self.payloads = []

    async def post(self, url, *, headers, json):
        self.payloads.append(json)
        return FakeResponse(400, text='{"error":"bad image"}')


json_module = json


def _settings():
    return SimpleNamespace(
        venice_api_key="test-key",
        venice_api_base_url="https://api.venice.ai/api/v1",
        vision_model="e2ee-qwen3-vl-30b-a3b-p",
        vision_request_timeout_seconds=45,
    )


def test_same_reviewer_retries_without_response_format_only_when_explicitly_unsupported(monkeypatch):
    async def run():
        settings = _settings()
        monkeypatch.setattr(vision_client, "get_settings", lambda: settings)
        client = UnsupportedThenJSONClient()

        result = await vision_client.analyze_image_bytes_with_venice(
            PNG_BYTES,
            prompt="Return JSON only",
            model=settings.vision_model,
            client=client,
        )

        assert len(client.payloads) == 2
        assert client.payloads[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in client.payloads[1]
        assert client.payloads[0]["messages"] == client.payloads[1]["messages"]
        assert result["confidence"] == "high"
        assert result["model"] == settings.vision_model

    asyncio.run(run())


def test_unrelated_bad_request_does_not_silently_drop_structured_mode(monkeypatch):
    async def run():
        settings = _settings()
        monkeypatch.setattr(vision_client, "get_settings", lambda: settings)
        client = OtherBadRequestClient()

        with pytest.raises(RuntimeError, match="vision_http_400"):
            await vision_client.analyze_image_bytes_with_venice(
                PNG_BYTES,
                prompt="Return JSON only",
                model=settings.vision_model,
                client=client,
            )

        assert len(client.payloads) == 1
        assert client.payloads[0]["response_format"] == {"type": "json_object"}

    asyncio.run(run())
