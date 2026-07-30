import asyncio
from types import SimpleNamespace

from app.services import generated_image_qa_service as qa_service
from app.services.generated_image_qa_service import GeneratedImageQAResult


IMAGE_BYTES = b"\x89PNG\r\n\x1a\nreviewer-pool-test"


def _settings(**overrides):
    values = {
        "venice_api_key": "test-key",
        "vision_model": "qwen3-vl-235b-a22b",
        "vision_fallback_model": "mistral-31-24b",
        "vision_reviewer_models": (
            "qwen3-vl-235b-a22b,"
            "mistral-31-24b,"
            "e2ee-qwen3-vl-30b-a3b-p"
        ),
        "image_generation_qa_attempts_per_model": 2,
        "image_generation_qa_max_reviewer_models": 3,
        "image_generation_qa_timeout_seconds": 50,
        "image_generation_anatomy_qa_attempts_per_model": 2,
        "image_generation_anatomy_max_reviewer_models": 3,
        "image_generation_anatomy_required_reviewers": 2,
        "image_generation_anatomy_qa_timeout_seconds": 50,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _composition_pass_payload():
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


def _anatomy_payload(*, passed=True):
    return {
        "anatomy_visible_enough_to_assess": True,
        "anatomy_consistent_with_profile": passed,
        "contradictory_sex_characteristics": False,
        "malformed_anatomy": not passed,
        "implausible_anatomy": False,
        "duplicated_anatomy_parts": False,
        "missing_expected_parts_when_visible": False,
        "ambiguous_anatomy": False,
        "confidence": "high",
        "reason_codes": [] if passed else ["malformed_anatomy"],
    }


def test_default_reviewer_pool_is_ordered_and_distinct():
    from app.core.config import Settings

    settings = Settings()
    assert settings.vision_model == "qwen3-vl-235b-a22b"
    assert settings.vision_fallback_model == "mistral-31-24b"
    assert qa_service._configured_vision_reviewer_models(settings) == [
        "qwen3-vl-235b-a22b",
        "mistral-31-24b",
        "e2ee-qwen3-vl-30b-a3b-p",
    ]


def test_pool_deduplicates_and_honors_max_models():
    settings = _settings(
        vision_reviewer_models=(
            "qwen3-vl-235b-a22b,mistral-31-24b,"
            "qwen3-vl-235b-a22b,e2ee-qwen3-vl-30b-a3b-p"
        )
    )
    assert qa_service._configured_vision_reviewer_models(
        settings,
        max_models=2,
    ) == ["qwen3-vl-235b-a22b", "mistral-31-24b"]


def test_visual_qa_uses_mistral_after_primary_transport_exhaustion(monkeypatch):
    async def run():
        settings = _settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if model == settings.vision_model:
                raise TimeoutError("primary unavailable")
            return _composition_pass_payload()

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_generated_image_composition(
            IMAGE_BYTES,
            expected_subject_count=1,
            visual_requirements={},
        )

        assert result.passed is True
        assert result.model == "mistral-31-24b"
        assert calls == [
            "qwen3-vl-235b-a22b",
            "qwen3-vl-235b-a22b",
            "mistral-31-24b",
        ]

    asyncio.run(run())


def test_anatomy_pool_replaces_transient_mistral_with_emergency_reviewer(monkeypatch):
    async def run():
        settings = _settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if model == "mistral-31-24b":
                raise TimeoutError("mistral transient outage")
            return _anatomy_payload(passed=True)

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            IMAGE_BYTES,
            anatomical_profile="female",
        )

        assert result.passed is True
        assert result.consensus_passed is True
        assert [item["model"] for item in result.qa_passes] == [
            "qwen3-vl-235b-a22b",
            "e2ee-qwen3-vl-30b-a3b-p",
        ]
        assert result.reviewer_failures == [
            {
                "model": "mistral-31-24b",
                "reason_codes": ["anatomy_qa_provider_failure"],
            }
        ]
        assert calls == [
            "qwen3-vl-235b-a22b",
            "mistral-31-24b",
            "mistral-31-24b",
            "e2ee-qwen3-vl-30b-a3b-p",
        ]

    asyncio.run(run())


def test_conclusive_anatomy_rejection_cannot_be_overruled_by_third_model(monkeypatch):
    async def run():
        settings = _settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if model == "mistral-31-24b":
                return _anatomy_payload(passed=False)
            return _anatomy_payload(passed=True)

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            IMAGE_BYTES,
            anatomical_profile="female",
        )

        assert result.passed is False
        assert result.consensus_passed is False
        assert "malformed_anatomy" in result.reason_codes
        assert calls == ["qwen3-vl-235b-a22b", "mistral-31-24b"]
        assert "e2ee-qwen3-vl-30b-a3b-p" not in calls

    asyncio.run(run())


def test_anatomy_pool_remains_fail_closed_without_two_successful_models(monkeypatch):
    async def run():
        settings = _settings()
        monkeypatch.setattr(qa_service, "get_settings", lambda: settings)
        calls = []

        async def analyze(image_bytes, *, prompt, model):
            calls.append(model)
            if model == "qwen3-vl-235b-a22b":
                return _anatomy_payload(passed=True)
            raise TimeoutError("reviewer unavailable")

        monkeypatch.setattr(qa_service, "analyze_image_bytes_with_venice", analyze)
        result = await qa_service.evaluate_adult_anatomy_image(
            IMAGE_BYTES,
            anatomical_profile="female",
        )

        assert result.passed is False
        assert result.consensus_passed is False
        assert "anatomy_qa_consensus_incomplete" in result.reason_codes
        assert result.qa_passes == []
        assert [item["model"] for item in result.partial_qa_passes] == [
            "qwen3-vl-235b-a22b"
        ]
        assert calls == [
            "qwen3-vl-235b-a22b",
            "mistral-31-24b",
            "mistral-31-24b",
            "e2ee-qwen3-vl-30b-a3b-p",
            "e2ee-qwen3-vl-30b-a3b-p",
        ]

    asyncio.run(run())


def test_pool_merge_requires_distinct_models():
    first = GeneratedImageQAResult(
        True,None,None,False,False,False,False,False,False,"high",[],"same-model",
        anatomy_visible_enough_to_assess=True,
        anatomy_consistent_with_profile=True,
        contradictory_sex_characteristics=False,
        malformed_anatomy=False,
        implausible_anatomy=False,
        duplicated_anatomy_parts=False,
        missing_expected_parts_when_visible=False,
        ambiguous_anatomy=False,
    )
    duplicate = GeneratedImageQAResult(
        True,None,None,False,False,False,False,False,False,"high",[],"same-model",
        anatomy_visible_enough_to_assess=True,
        anatomy_consistent_with_profile=True,
        contradictory_sex_characteristics=False,
        malformed_anatomy=False,
        implausible_anatomy=False,
        duplicated_anatomy_parts=False,
        missing_expected_parts_when_visible=False,
        ambiguous_anatomy=False,
    )
    result = qa_service.merge_adult_anatomy_reviewer_pool(
        [first, duplicate],
        required_reviewers=2,
    )
    assert result.passed is False
    assert "anatomy_qa_consensus_incomplete" in result.reason_codes
