import asyncio
import json
from types import SimpleNamespace

from app.llm import vision_client
from app.services import generated_image_qa_service as qa_service


PNG_BYTES = b"\x89PNG\r\n\x1a\nvision-test"


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "person_count": 1,
                                "face_count": 1,
                                "confidence": "high",
                                "framing": "full_body",
                                "framing_matches_request": True,
                            }
                        )
                    }
                }
            ]
        }


class RecordingHTTPClient:
    def __init__(self):
        self.url = None
        self.headers = None
        self.payload = None

    async def post(self, url, *, headers, json):
        self.url = url
        self.headers = headers
        self.payload = json
        return FakeResponse()


def _vision_settings(**overrides):
    values = {
        "venice_api_key": "test-key",
        "venice_api_base_url": "https://api.venice.ai/api/v1",
        "vision_model": "qwen3-vl-235b-a22b",
        "vision_fallback_model": "e2ee-qwen3-vl-30b-a3b-p",
        "vision_request_timeout_seconds": 45,
        "image_generation_qa_timeout_seconds": 50,
        "image_generation_qa_attempts_per_model": 2,
        "image_generation_anatomy_qa_timeout_seconds": 50,
        "image_generation_anatomy_qa_attempts_per_model": 2,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _valid_composition_payload():
    return {
        "person_count": 1,
        "face_count": 1,
        "intended_subject_count": 1,
        "unexpected_additional_person_visible": False,
        "background_extra_person_visible": False,
        "duplicate_subject_visible": False,
        "reflection_visible": False,
        "reflected_distinct_person_visible": False,
        "selfie_detected": False,
        "mirror_selfie_detected": False,
        "confidence": "high",
        "framing": "medium",
        "framing_matches_request": True,
        "natural_capture_plausible": True,
        "looks_like_id_photo": False,
        "reason_codes": [],
    }


def _valid_anatomy_payload():
    return {
        "anatomy_visible_enough_to_assess": True,
        "anatomy_consistent_with_profile": True,
        "contradictory_sex_characteristics": False,
        "malformed_anatomy": False,
        "implausible_anatomy": False,
        "duplicated_anatomy_parts": False,
        "missing_expected_parts_when_visible": False,
        "ambiguous_anatomy": False,
        "confidence": "high",
        "reason_codes": [],
    }


def test_vision_client_uses_actual_png_mime_and_structured_json(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(vision_client, "get_settings", lambda: settings)
        client = RecordingHTTPClient()

        result = await vision_client.analyze_image_bytes_with_venice(
            PNG_BYTES,
            prompt="Return JSON only",
            model=settings.vision_model,
            client=client,
        )

        payload = client.payload
        image_url = payload["messages"][0]["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["venice_parameters"] == {
            "include_venice_system_prompt": False,
            "disable_thinking": True,
            "strip_thinking_response": True,
        }
        assert payload["max_tokens"] == 900
        assert result["model"] == settings.vision_model
        assert result["person_count"] == 1

    asyncio.run(run())


def test_composition_qa_retries_same_reviewer_after_transport_error(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if len(calls) == 1:
                raise TimeoutError("temporary vision timeout")
            return _valid_composition_payload()

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_generated_image_composition(
            PNG_BYTES,
            expected_subject_count=1,
            visual_requirements={},
        )

        assert result.passed is True
        assert calls == [settings.vision_model, settings.vision_model]

    asyncio.run(run())


def test_composition_qa_retries_incomplete_json_on_same_image(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        payloads = [
            {"person_count": 1, "confidence": "high"},
            _valid_composition_payload(),
        ]
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append((image_bytes, model))
            return payloads.pop(0)

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_generated_image_composition(
            PNG_BYTES,
            expected_subject_count=1,
            visual_requirements={},
        )

        assert result.passed is True
        assert len(calls) == 2
        assert calls[0][0] == calls[1][0] == PNG_BYTES
        assert calls[0][1] == calls[1][1] == settings.vision_model

    asyncio.run(run())


def test_composition_qa_remains_fail_closed_after_all_reviewer_retries(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            raise TimeoutError("reviewer unavailable")

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_generated_image_composition(
            PNG_BYTES,
            expected_subject_count=1,
            visual_requirements={},
        )

        assert result.passed is False
        assert "qa_provider_failure" in result.reason_codes
        assert calls == [
            settings.vision_model,
            settings.vision_model,
            settings.vision_fallback_model,
            settings.vision_fallback_model,
        ]

    asyncio.run(run())


def test_anatomy_qa_retries_each_independent_reviewer_then_passes(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        per_model_calls = {}

        async def analyze(image_bytes, *, prompt, model):
            per_model_calls[model] = per_model_calls.get(model, 0) + 1
            if per_model_calls[model] == 1:
                raise TimeoutError("first reviewer request timed out")
            return _valid_anatomy_payload()

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            PNG_BYTES,
            anatomical_profile="female",
            user_id=1,
            job_id=2,
            request_chain_id="chain",
        )

        assert result.passed is True
        assert result.consensus_passed is True
        assert len(result.qa_passes) == 2
        assert per_model_calls == {
            settings.vision_model: 2,
            settings.vision_fallback_model: 2,
        }

    asyncio.run(run())


def test_anatomy_qa_retries_malformed_payload_then_passes(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if calls.count(model) == 1:
                return {"confidence": "low"}
            return _valid_anatomy_payload()

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            PNG_BYTES,
            anatomical_profile="female",
        )

        assert result.passed is True
        assert calls.count(settings.vision_model) == 2
        assert calls.count(settings.vision_fallback_model) == 2

    asyncio.run(run())


def test_anatomy_qa_stays_fail_closed_when_one_reviewer_is_exhausted(monkeypatch):
    async def run():
        settings = _vision_settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if model == settings.vision_fallback_model:
                raise TimeoutError("fallback reviewer unavailable")
            return _valid_anatomy_payload()

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            PNG_BYTES,
            anatomical_profile="female",
        )

        assert result.passed is False
        assert result.consensus_passed is False
        assert "anatomy_qa_provider_failure" in result.reason_codes
        assert calls == [
            settings.vision_model,
            settings.vision_fallback_model,
            settings.vision_fallback_model,
        ]

    asyncio.run(run())


def test_anatomy_consensus_requires_two_distinct_model_ids(monkeypatch):
    async def run():
        settings = _vision_settings(
            vision_fallback_model="qwen3-vl-235b-a22b",
        )
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)

        async def analyze(image_bytes, *, prompt, model):
            return _valid_anatomy_payload()

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            PNG_BYTES,
            anatomical_profile="female",
        )

        assert result.passed is False
        assert "anatomy_qa_consensus_incomplete" in result.reason_codes
        assert len(result.qa_passes) == 0

    asyncio.run(run())
